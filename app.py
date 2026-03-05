import io
import os
import re
import zipfile
import json
import requests
import pandas as pd
import streamlit as st

st.set_page_config(page_title="AI華語拍檔-人工評閱華語教材語篇品質評分平台", layout="wide")

st.set_page_config(
    page_title="AI華語拍檔-人工評閱華語教材語篇品質評分平台",
    layout="wide"
)

st.markdown("""
<style>

/* Streamlit text_area（包含 disabled）文字全黑 */
div[data-testid="stTextArea"] textarea {
    color: #000 !important;
    -webkit-text-fill-color: #000 !important;
    opacity: 1 !important;
    font-size: 17px !important;
    line-height: 1.8 !important;
}

/* disabled 的 text_area 也強制黑字 */
textarea:disabled {
    color: #000 !important;
    -webkit-text-fill-color: #000 !important;
    opacity: 1 !important;
}

/* 一般文字 */
p, span, label, div {
    color: #000 !important;
}

/* Markdown 標題 */
h1, h2, h3, h4, h5 {
    color: #000 !important;
}

/* Sidebar 文字 */
section[data-testid="stSidebar"] * {
    color: #000 !important;
}

</style>
""", unsafe_allow_html=True)

col1, col2 = st.columns([1,6])

with col1:
    st.image("華語拍檔LOGO去背檔（白底純頭像）.png", width=100)

with col2:
    st.title("AI華語拍檔-人工評閱華語教材語篇品質評分平台")

# ===== Secrets =====
SUPABASE_URL = st.secrets.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = st.secrets.get("SUPABASE_ANON_KEY", "")

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    st.error("缺少 Secrets：SUPABASE_URL 或 SUPABASE_ANON_KEY。請到 Streamlit → Settings → Secrets 設定。")
    st.stop()

REST_BASE = f"{SUPABASE_URL}/rest/v1"

HEADERS = {
    "apikey": SUPABASE_ANON_KEY,
    "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
    "Content-Type": "application/json",
}

SCORE_OPTIONS = [0, 1, 2, 3]
GRADE_OPTIONS = ["A", "B", "C"]

# ===== Supabase REST helpers =====
def _req(method: str, path: str, params=None, data=None):
    url = f"{REST_BASE}/{path}"
    try:
        r = requests.request(
            method,
            url,
            headers=HEADERS,
            params=params,
            data=data,
            timeout=20,
        )
        # 把錯誤內容也顯示出來，方便你排錯
        if not r.ok:
            raise RuntimeError(f"Supabase API 失敗：{r.status_code}\n{r.text}")
        return r
    except requests.RequestException as e:
        raise RuntimeError(f"連線 Supabase 失敗：{e}")

def sb_get(table, select="*", params=None):
    q = {"select": select}
    if params:
        q.update(params)
    r = _req("GET", table, params=q)
    return r.json()

def sb_upsert(table, rows, on_conflict=None):
    headers = dict(HEADERS)
    headers["Prefer"] = "resolution=merge-duplicates"
    url = f"{REST_BASE}/{table}"
    params = {}
    if on_conflict:
        params["on_conflict"] = on_conflict
    try:
        r = requests.post(url, headers=headers, params=params, data=json.dumps(rows), timeout=20)
        if not r.ok:
            raise RuntimeError(f"Supabase upsert 失敗：{r.status_code}\n{r.text}")
        return r.json() if r.text else []
    except requests.RequestException as e:
        raise RuntimeError(f"連線 Supabase 失敗：{e}")

def sb_patch(table, match_params, patch_obj):
    r = _req("PATCH", table, params=match_params, data=json.dumps(patch_obj))
    return r.json() if r.text else []

# ===== TXT parser =====
def parse_tbcl_txt(text: str):
    def get(pattern, flags=0):
        m = re.search(pattern, text, flags)
        return m.group(1).strip() if m else ""

    tbcl_level = get(r"【TBCL等級】\s*(.*)")
    article_type = get(r"【文章類型】\s*(.*)")
    extra_info = get(r"【額外資訊】\s*(.*)")

    before_block = get(r"【修改前文章】\s*(.*?)\n\s*----------------------------------------", flags=re.S)
    after_block = get(r"【修改後文章】\s*(.*)$", flags=re.S)

    def split_title_content(block: str):
        if not block:
            return "", ""
        title = ""
        content = ""
        mt = re.search(r"標題：\s*(.*)", block)
        if mt:
            title = mt.group(1).strip()
        mc = re.search(r"內容：\s*(.*)", block, flags=re.S)
        if mc:
            content = mc.group(1).strip()
        return title, content

    before_title, before_content = split_title_content(before_block)
    after_title, after_content = split_title_content(after_block)

    return {
        "tbcl_level": tbcl_level,
        "article_type": article_type,
        "extra_info": extra_info,
        "before_title": before_title,
        "before_content": before_content,
        "after_title": after_title,
        "after_content": after_content,
    }

def read_uploaded_as_txt_or_zip(uploaded_file):
    name = uploaded_file.name
    data = uploaded_file.read()
    if name.lower().endswith(".zip"):
        out = []
        with zipfile.ZipFile(io.BytesIO(data), "r") as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                if info.filename.lower().endswith(".txt"):
                    out.append((os.path.basename(info.filename), z.read(info.filename)))
        return out
    return [(name, data)]

# ===== Domain logic =====
def get_reviewers(active_only=True):
    params = {"order": "name.asc"}
    if active_only:
        params["is_active"] = "eq.true"
    return sb_get("reviewers", select="reviewer_id,name,is_active", params=params)

def init_reviews_for_article(article_id: str, reviewer_ids):
    rows = [{"article_id": article_id, "reviewer_id": rid, "status": "not_started"} for rid in reviewer_ids]
    sb_upsert("reviews", rows, on_conflict="article_id,reviewer_id")

def import_txt(files):
    reviewers = get_reviewers(active_only=True)
    reviewer_ids = [r["reviewer_id"] for r in reviewers]
    if not reviewer_ids:
        return 0, [("（系統）", "reviewers 表沒有任何 active 老師，請先新增 5 位老師姓名")]

    errors = []
    ok = 0
    for fname, b in files:
        try:
            text = b.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = b.decode("utf-8-sig")
            except Exception:
                errors.append((fname, "無法以 UTF-8 解碼"))
                continue

        fields = parse_tbcl_txt(text)
        row = {"id": fname, **fields}

        sb_upsert("articles", [row], on_conflict="id")
        init_reviews_for_article(fname, reviewer_ids)

        ok += 1
        if not fields["tbcl_level"] or not fields["article_type"]:
            errors.append((fname, "缺少 TBCL等級 或 文章類型（仍已入庫）"))
    return ok, errors

def get_progress(reviewer_id: int):
    total = len(sb_get("articles", select="id"))
    done = len(sb_get("reviews", select="review_id", params={"reviewer_id": f"eq.{reviewer_id}", "status": "eq.submitted"}))
    return done, total

def get_next_article(reviewer_id: int):
    rows = sb_get(
        "reviews",
        select="article_id,status",
        params={"reviewer_id": f"eq.{reviewer_id}", "status": "neq.submitted", "order": "article_id.asc", "limit": 1},
    )
    if not rows:
        return None
    aid = rows[0]["article_id"]
    art = sb_get("articles", select="*", params={"id": f"eq.{aid}", "limit": 1})
    return art[0] if art else None

def get_review(reviewer_id: int, article_id: str):
    rows = sb_get("reviews", select="*", params={"reviewer_id": f"eq.{reviewer_id}", "article_id": f"eq.{article_id}", "limit": 1})
    return rows[0] if rows else None

def save_review(reviewer_id: int, article_id: str, payload: dict, submitted: bool):
    before_total = payload["before_lang_score"] + payload["before_logic_score"] + payload["before_value_score"]
    after_total = payload["after_lang_score"] + payload["after_logic_score"] + payload["after_value_score"]
    patch = {
        **payload,
        "before_total_score": before_total,
        "after_total_score": after_total,
        "status": "submitted" if submitted else "in_progress",
    }
    sb_patch("reviews", {"reviewer_id": f"eq.{reviewer_id}", "article_id": f"eq.{article_id}"}, patch)

def export_all_df():
    articles = sb_get("articles", select="*")
    reviews = sb_get("reviews", select="*")
    reviewers = sb_get("reviewers", select="reviewer_id,name")

    df_a = pd.DataFrame(articles)
    df_r = pd.DataFrame(reviews)
    df_u = pd.DataFrame(reviewers).rename(columns={"name": "評分老師_姓名"})

    df = df_r.merge(df_a, left_on="article_id", right_on="id", how="left").merge(df_u, on="reviewer_id", how="left")

    out = pd.DataFrame({
        "id": df["id"],
        "TBCL等級": df["tbcl_level"],
        "文章類型": df["article_type"],
        "額外資訊": df["extra_info"],
        "修改前文章_標題": df["before_title"],
        "修改前文章_內容": df["before_content"],
        "修改前文章_語言自然度／符合規範_評分": df["before_lang_score"],
        "修改前文章_語言自然度／符合規範_等第": df["before_lang_grade"],
        "修改前文章_邏輯與結構_評分": df["before_logic_score"],
        "修改前文章_邏輯與結構_等第": df["before_logic_grade"],
        "修改前文章_教學價值_評分": df["before_value_score"],
        "修改前文章_教學價值_等第": df["before_value_grade"],
        "修改前文章_總分": df["before_total_score"],
        "修改後文章_標題": df["after_title"],
        "修改後文章_內容": df["after_content"],
        "修改後文章_語言自然度／符合規範_評分": df["after_lang_score"],
        "修改後文章_語言自然度／符合規範_等第": df["after_lang_grade"],
        "修改後文章_邏輯與結構_評分": df["after_logic_score"],
        "修改後文章_邏輯與結構_等第": df["after_logic_grade"],
        "修改後文章_教學價值_評分": df["after_value_score"],
        "修改後文章_教學價值_等第": df["after_value_grade"],
        "修改後文章_總分": df["after_total_score"],
        "評分老師_姓名": df["評分老師_姓名"],
        "最後儲存時間": df["updated_at"],
    })
    return out.sort_values(["id", "評分老師_姓名"], na_position="last")

# ===== UI (with guard) =====
try:
    mode = st.sidebar.radio("模式", ["評分老師端", "維護者後台端"])

    if mode == "維護者後台端":
        st.subheader("維護者後台端")

        st.markdown("### 1) 一鍵匯入文章（txt 或 zip）")
        uploaded = st.file_uploader("上傳 txt 或 zip（可多選）", type=["txt", "zip"], accept_multiple_files=True)
        if uploaded:
            all_files = []
            for f in uploaded:
                all_files.extend(read_uploaded_as_txt_or_zip(f))

            if st.button("開始匯入"):
                with st.spinner("匯入中..."):
                    ok, errors = import_txt(all_files)
                st.success(f"完成匯入：{ok} 篇")
                if errors:
                    st.warning("以下檔案可能缺欄或格式不完整：")
                    st.dataframe(pd.DataFrame(errors, columns=["檔名", "原因"]), use_container_width=True)

        st.markdown("### 2) 進度總覽")
        reviewers = get_reviewers(active_only=True)
        if not reviewers:
            st.error("reviewers 表沒有任何 active 老師，請先新增 5 位老師姓名（is_active=true）。")
        else:
            total_articles = len(sb_get("articles", select="id"))
            st.write(f"文章總數：**{total_articles}**")
            st.write(f"預期評分總數（文章×老師）：**{total_articles * len(reviewers)}**")

            prog = []
            for r in reviewers:
                done, total = get_progress(r["reviewer_id"])
                prog.append({"老師": r["name"], "已提交": done, "應提交": total, "完成率": (done / total) if total else 0})
            st.dataframe(pd.DataFrame(prog), use_container_width=True)

        st.markdown("### 3) 一鍵匯出所有評分結果 CSV")
        if st.button("產生匯出檔"):
            df = export_all_df()
            csv_bytes = df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
            st.download_button("下載 CSV（全體評分結果）", data=csv_bytes, file_name="ai_grading_results.csv", mime="text/csv")

    else:
        st.subheader("評分老師端")

        reviewers = get_reviewers(active_only=True)
        if not reviewers:
            st.error("目前沒有老師名單。請維護者先在 reviewers 表新增 5 位老師姓名（is_active=true）。")
            st.stop()

        name_list = [r["name"] for r in reviewers]
        teacher_name = st.selectbox("請選擇你的姓名登入，別選錯了喔！", name_list)

        reviewer_id = [r["reviewer_id"] for r in reviewers if r["name"] == teacher_name][0]
        done, total = get_progress(reviewer_id)

        st.markdown("### 進度")
        st.write(f"已提交：**{done}** / 應提交：**{total}**")
        if total > 0:
            st.progress(done / total)

        st.markdown("---")

        article = get_next_article(reviewer_id)
        if not article:
            st.success("你已完成所有文章的評分，謝謝！")
            st.stop()

        article_id = article["id"]
        review = get_review(reviewer_id, article_id) or {}

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("#### 修改前文章")
            st.markdown(f"**標題：** {article.get('before_title','')}")
            st.text_area("內容（修改前）", value=article.get("before_content", ""), height=280, disabled=True)
        with col2:
            st.markdown("#### 修改後文章")
            st.markdown(f"**標題：** {article.get('after_title','')}")
            st.text_area("內容（修改後）", value=article.get("after_content", ""), height=280, disabled=True)

        st.markdown("### 評分表單（12 欄位皆需填寫）")

        def def_int(key):
            v = review.get(key)
            return int(v) if v is not None else 0

        def def_grade(key):
            v = review.get(key)
            return v if v in GRADE_OPTIONS else "A"

        st.markdown("#### 修改前文章")
        b1, b2, b3 = st.columns(3)
        with b1:
            before_lang_score = st.selectbox("語言自然度／符合規範_評分", SCORE_OPTIONS, index=SCORE_OPTIONS.index(def_int("before_lang_score")))
            before_lang_grade = st.selectbox("語言自然度／符合規範_等第", GRADE_OPTIONS, index=GRADE_OPTIONS.index(def_grade("before_lang_grade")))
        with b2:
            before_logic_score = st.selectbox("邏輯與結構_評分", SCORE_OPTIONS, index=SCORE_OPTIONS.index(def_int("before_logic_score")))
            before_logic_grade = st.selectbox("邏輯與結構_等第", GRADE_OPTIONS, index=GRADE_OPTIONS.index(def_grade("before_logic_grade")))
        with b3:
            before_value_score = st.selectbox("教學價值_評分", SCORE_OPTIONS, index=SCORE_OPTIONS.index(def_int("before_value_score")))
            before_value_grade = st.selectbox("教學價值_等第", GRADE_OPTIONS, index=GRADE_OPTIONS.index(def_grade("before_value_grade")))

        before_total = before_lang_score + before_logic_score + before_value_score
        st.metric("修改前文章_總分（自動加總）", before_total)

        st.markdown("#### 修改後文章")
        a1, a2, a3 = st.columns(3)
        with a1:
            after_lang_score = st.selectbox("語言自然度／符合規範_評分（修改後）", SCORE_OPTIONS, index=SCORE_OPTIONS.index(def_int("after_lang_score")))
            after_lang_grade = st.selectbox("語言自然度／符合規範_等第（修改後）", GRADE_OPTIONS, index=GRADE_OPTIONS.index(def_grade("after_lang_grade")))
        with a2:
            after_logic_score = st.selectbox("邏輯與結構_評分（修改後）", SCORE_OPTIONS, index=SCORE_OPTIONS.index(def_int("after_logic_score")))
            after_logic_grade = st.selectbox("邏輯與結構_等第（修改後）", GRADE_OPTIONS, index=GRADE_OPTIONS.index(def_grade("after_logic_grade")))
        with a3:
            after_value_score = st.selectbox("教學價值_評分（修改後）", SCORE_OPTIONS, index=SCORE_OPTIONS.index(def_int("after_value_score")))
            after_value_grade = st.selectbox("教學價值_等第（修改後）", GRADE_OPTIONS, index=GRADE_OPTIONS.index(def_grade("after_value_grade")))

        after_total = after_lang_score + after_logic_score + after_value_score
        st.metric("修改後文章_總分（自動加總）", after_total)

        comment = st.text_area("留言（非必填）", value=review.get("comment") or "", height=120)

        payload = {
            "before_lang_score": before_lang_score,
            "before_lang_grade": before_lang_grade,
            "before_logic_score": before_logic_score,
            "before_logic_grade": before_logic_grade,
            "before_value_score": before_value_score,
            "before_value_grade": before_value_grade,
            "after_lang_score": after_lang_score,
            "after_lang_grade": after_lang_grade,
            "after_logic_score": after_logic_score,
            "after_logic_grade": after_logic_grade,
            "after_value_score": after_value_score,
            "after_value_grade": after_value_grade,
            "comment": comment,
        }

        c1, c2 = st.columns(2)
        with c1:
            if st.button("儲存（不提交）"):
                save_review(reviewer_id, article_id, payload, submitted=False)
                st.success("已儲存（未提交）。")
                st.rerun()
        with c2:
            if st.button("提交並下一篇"):
                save_review(reviewer_id, article_id, payload, submitted=True)
                st.success("已提交，前往下一篇…")
                st.rerun()

except Exception as e:
    st.error("App 啟動時發生錯誤（但我沒有讓它變成 503）。")
    st.exception(e)
