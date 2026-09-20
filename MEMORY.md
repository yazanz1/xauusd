# Memory — xaubot

עודכן לאחרונה: 2026-09-20 (watchdog entry path + logs)

זה קובץ הזיכרון החי של האסטרטגיה. בכל שיפור — לעדכן כאן **ואת** `STRATEGY.md`.

## מצב נוכחי

- העתקת איתותים מ-`gold_trades` ל-MT5 (VPS/מקומי)
- סריקה כל `POLL_SEC` (ברירת מחדל 5 שניות), מיד בהפעלה
- איתות חדש: `open` + אין `mt5_ticket`
- כניסה בשוק: long=BUY, short=SELL עם SL/TP מהשורה
- ברירת מחדל `DRY_RUN=0` — מסחר חי ופתיחת עסקאות תמיד (אלא אם מגדירים 1 במפורש)
- הגנות: פילטר שעות ישראל (`12:00–14:30`, `22:00–00:00`), `MAX_AGE_SEC`, `MAX_SLIP_USD`, `MAX_OPEN`, claim על `copied_at`
- פילטרים ב־`filters/`: `israel_hours`, `london_after_ny`, `us_holiday_next_day`, `ny_open` (15:20–15:30 IDT)
- אחרי מילוי: `mt5_ticket` + `mt5_fill_price` (לא דורסים `entry` של היומן)
- נעילה 40/35 מקומית: לפני set_sl — normalize ל־digits, בדיקת צד (לונג SL<bid / שורט SL>ask), רצפת stops×1.2, לוג retcode מלא
- סגירה מ-MT5 → Supabase: deals לפי `position_id` אחרי טעינת היסטוריה; `exit_reason` לפי מחיר מול stop/tp/lock (לא מיפוי reason שבור)
- יתומים: פוזיציית MAGIC ב־MT5 בלי שורת open → לוג ORPHAN (לא סוגר)
- סנכרון פתוחות: PATCH לסופבייס רק אם profit/SL/TP השתנו (לא כל 5 שניות סתם)
- Gann/statics: cache מקומי אחרי שמירה — בלי SELECT חוזר בכל poll
- בסוף כל יום (אחרי 00:05 ישראל) סיכום ל־`gold_statics`: tp/sl/lock + רווח + פיפס
- Gann נשמר ב-`gann_levels` בלי לפתוח ממנו
- Heartbeat: אחרי כל סבב מוצלח נכתב `heartbeat.txt` ליד `bot.py`; `watchdog.ps1` מפעיל מחדש אם הדופק ישן
- Watchdog: `EntryScript` מלא + redirect לוגים בריפו; ב־VPS רק `watchdog.local.ps1` ל־`$Python`/`$StaleSec` (אחרי בדיקת heartbeat → 180)

## החלטות שחייבות להישאר

- service role וסיסמת MT5 ב-`.env` בלבד
- חיבור MT5 מדליק אוטומטית Algo Trading אם כבוי (Ctrl+E)
- ברירת מחדל `DRY_RUN=0` (מסחר חי); אפשר `DRY_RUN=1` רק לבדיקה בלי פקודות
- לא לפתוח אותו `id` פעמיים (`mt5_ticket` / claim)
- לא להשאיר שני טרמינלי MT5 פתוחים במקביל
- לא להריץ מול לייב עם שורות בדיקה פתוחות
- נעילה לפי webhook entry (לא fill) לדיוק מול היומן; בלי `modify` מהפיין

## כשמשפרים אסטרטגיה

1. לעדכן את הבלוק "מצב נוכחי" כאן
2. לעדכן את `STRATEGY.md` (כללים + שורת changelog עם התאריך)
3. לעדכן את `.cursor/rules/xaubot-strategy.mdc` אם כלל הביצוע השתנה
