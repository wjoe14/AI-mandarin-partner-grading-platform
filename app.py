這份版本使用 Supabase 的 Data API（REST）連線，不需要你找 DATABASE_URL。
功能含：
✅ 後台匯入 txt/zip（解析後寫入 articles）
✅ 自動為每篇文章×5位老師建立 reviews 任務（如果不存在）
✅ 老師端：姓名登入（從 reviewers 表）
✅ 12 個下拉欄位（0-3 / A-B-C）
✅ 自動算修改前/修改後總分
✅ 進度條
✅ 後台一鍵匯出 CSV
