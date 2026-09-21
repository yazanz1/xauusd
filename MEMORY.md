# Memory — xaubot

עודכן לאחרונה: 2026-09-21 (journal vs MT5 split)

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
- נעילה 40/35: נוסחה מ־`entry`+`tp`. לפני set_sl — אם SL בצד הלא נכון של המחיר, דילוג לפני הקלאמפ. הקלאמפ לא חוצה `mt5_fill_price`
- מעקב: `mt5_ticket` + `mt5_closed_at` ריק + `date_idt` ב־7 ימים. לא לפי `status`
- סגירת MT5 כותבת רק `mt5_close_price` / `mt5_closed_at` (UTC) / `mt5_profit` / `mt5_exit_reason`. Pine שומר `status` / `exit` / `exit_reason` / `pips` / `usd_0_3`
- `deal.time` = שעון שרת; המרה ל-UTC בשעות שלמות, cache כשהטיק ישן
- יתומים: פוזיציית MAGIC בלי שורה במעקב → לוג ORPHAN (לא סוגר)
- סנכרון פתוחות: PATCH ל־`mt5_profit` רק אם השתנה
- Gann/statics: cache מקומי אחרי שמירה — בלי SELECT חוזר בכל poll
- בסוף כל יום (אחרי 00:05 ישראל) סיכום ל־`gold_statics`: tp/sl/lock + רווח + פיפס
- Gann נשמר ב-`gann_levels` בלי לפתוח ממנו
- Heartbeat: אחרי כל סבב מוצלח נכתב `heartbeat.txt` ליד `bot.py`; `watchdog.ps1` מפעיל מחדש אם הדופק ישן
- Watchdog: `EntryScript` מלא + redirect לוגים בריפו; ב־VPS רק `watchdog.local.ps1` ל־`$Python`/`$StaleSec` (אחרי בדיקת heartbeat → 180)

## נקודת חיתוך — 2026-09-21

לפני התאריך, בשורות עם `mt5_ticket`, `exit`/`exit_reason` משקפים MT5 (נדרסו). אי אפשר לשחזר את ה-Pine המקורי.  
מהתאריך: `exit`/`exit_reason` של ה-Pine; הביצוע ב־`mt5_*`. שורות בלי `mt5_ticket` תמיד Pine.

## החלטות שחייבות להישאר

- service role וסיסמת MT5 ב-`.env` בלבד
- חיבור MT5 מדליק אוטומטית Algo Trading אם כבוי (Ctrl+E)
- ברירת מחדל `DRY_RUN=0` (מסחר חי); אפשר `DRY_RUN=1` רק לבדיקה בלי פקודות
- לא לפתוח אותו `id` פעמיים (`mt5_ticket` / claim)
- לא להשאיר שני טרמינלי MT5 פתוחים במקביל
- לא להריץ מול לייב עם שורות בדיקה פתוחות
- נוסחת 40/35 מ־`entry`+`tp` של הוובהוק; רצפת הקלאמפ היא `mt5_fill_price`

## כשמשפרים אסטרטגיה

1. לעדכן את הבלוק "מצב נוכחי" כאן
2. לעדכן את `STRATEGY.md` (כללים + שורת changelog עם התאריך)
3. לעדכן את `.cursor/rules/xaubot-strategy.mdc` אם כלל הביצוע השתנה
