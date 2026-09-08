# Memory — xaubot

עודכן לאחרונה: 2026-09-08

זה קובץ הזיכרון החי של האסטרטגיה. בכל שיפור — לעדכן כאן **ואת** `STRATEGY.md`.

## מצב נוכחי

- העתקת איתותים מ-`gold_trades` ל-MT5 (VPS/מקומי)
- סריקה כל `POLL_SEC` (ברירת מחדל 5 שניות), מיד בהפעלה
- איתות חדש: `open` + אין `mt5_ticket`
- כניסה בשוק: long=BUY, short=SELL עם SL/TP מהשורה
- הגנות: `MAX_AGE_SEC`, `MAX_SLIP_USD`, `MAX_OPEN`, `DRY_RUN`, claim על `copied_at`
- אחרי מילוי: `mt5_ticket` + `mt5_fill_price` (לא דורסים `entry` של היומן)
- סגירה מ-MT5 → Supabase: `mt5_close_*` / `mt5_profit` + `exit`/`pips`/`usd_0_3`
- אין trailing/lock מהבוט; Gann נשמר ב-`gann_levels` בלי לפתוח ממנו

## החלטות שחייבות להישאר

- service role וסיסמת MT5 ב-`.env` בלבד
- חיבור MT5 מדליק אוטומטית Algo Trading אם כבוי (Ctrl+E)
- להתחיל עם `DRY_RUN=1` לפני מסחר חי
- לא לפתוח אותו `id` פעמיים (`mt5_ticket` / claim)
- לא להשאיר שני טרמינלי MT5 פתוחים במקביל
- לא להריץ מול לייב עם שורות בדיקה פתוחות

## כשמשפרים אסטרטגיה

1. לעדכן את הבלוק "מצב נוכחי" כאן
2. לעדכן את `STRATEGY.md` (כללים + שורת changelog עם התאריך)
3. לעדכן את `.cursor/rules/xaubot-strategy.mdc` אם כלל הביצוע השתנה
