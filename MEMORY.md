# Memory — xaubot

עודכן לאחרונה: 2026-11-23 (preopen + PINE_EXIT_SYNC 3s)

זה קובץ הזיכרון החי של האסטרטגיה. בכל שיפור — לעדכן כאן **ואת** `STRATEGY.md`.

## מצב נוכחי

- העתקת איתותים מ-`wick_strategy` ל-MT5 (VPS/מקומי); `MAGIC=260860`
- `active_filters()` תמיד `[]` — סינון כניסה ב־Pine בלבד (`STRATEGY` ב־`.env` לא משפיע)
- סריקה כל `POLL_SEC` (ברירת מחדל 5 שניות), מיד בהפעלה; לוג עלייה: `wick table=wick_strategy magic=260860 filters=[]`
- איתות חדש: `open` + אין `mt5_ticket` + אין `copied_at`
- כניסה בשוק: long=BUY, short=SELL עם SL/TP מהשורה
- נפח: `LOT_SIZE × lot_mult` (חסר / לא תקין / 0 → 1; תקרה 1, רצפה 0.05). `LOT_SIZE` ב־`.env` לא משתנה
- `preopen_check` (`PREOPEN_CHECK=1`; העלאה ראשונה: `PREOPEN_MODE=shadow`): דילוג סופי אם Pine כבר סגר / נגע SL/TP / עבר נעילה; `OPEN_LOCKED` עם SL=30% או סטופ מקורי אם lock צפוף ל־stops. `copied_at` רק בסופי/הצלחה — slip/`max_open` משאירים null ל־retry
- ברירת מחדל `DRY_RUN=0` — מסחר חי (לבדיקה: `DRY_RUN=1` ולוודא `mt5_error` עם "DRY_RUN would …")
- הגנות: `MAX_AGE_SEC=180` (תקרה), סליפג׳ `min(MAX_SLIP_USD=1.5, MAX_SLIP_STOP_FRAC=0.20 × |entry−stop|)`, `MAX_OPEN=6`, claim על `copied_at`
- אחרי מילוי: `mt5_ticket` + `mt5_fill_price` + `bot_lock_state=none` + `orig_stop` (לא דורסים `entry`)
- נעילה **40/30** על `bot_lock_*` בלבד: זיהוי M1 מ־`entry_time+5m` (לא `copied_at`); `set_sl` באותו poll; אם מחיר כבר עבר 30% → סגירה בשוק כ־lock (לא דילוג/retry); retry רק כש־min_dist דוחה ועדיין בצד הנכון
- לא קוראים את `stop` אחרי פתיחה לנעילה/סיווג (הוובהוק עלול לדרוס ל־lock_sl) — רק `orig_stop` / `bot_lock_sl`
- מעקב: `mt5_ticket` + `mt5_closed_at` ריק + `date_idt` ב־7 ימים. לא לפי `status`
- `PINE_EXIT_SYNC`: אם `status=closed` והפוזיציה עדיין פתוחה >`PINE_EXIT_SYNC_SEC` (ברירת מחדל 3) מ־`updated_at` → סגירה בשוק; `safe_close` לא כותב `mt5_error` על 10036
- סגירת MT5 כותבת רק `mt5_close_price` / `mt5_closed_at` (UTC) / `mt5_profit` / `mt5_exit_reason`
- יתומים: פוזיציית MAGIC 260860 בלי שורה במעקב → לוג ORPHAN (לא סוגר)
- סטטיסטיקה יומית מ־`wick_strategy` → `gold_statics`
- Heartbeat + watchdog כמו קודם

## נקודת חיתוך — 2026-09-21

לפני התאריך, בשורות עם `mt5_ticket`, `exit`/`exit_reason` משקפים MT5 (נדרסו). אי אפשר לשחזר את ה-Pine המקורי.  
מהתאריך: `exit`/`exit_reason` של ה-Pine; הביצוע ב־`mt5_*`. שורות בלי `mt5_ticket` תמיד Pine.

## יומן VPS

כל העלאה ל־VPS מתועדת ב־[VPS_UPDATES.md](VPS_UPDATES.md).  
⭐ חשוב להשוואה: cutover wick 40/30 + `bot_lock_*` (2026-11-22); וגם 50/30→40/35 (2026-09-12).

## החלטות שחייבות להישאר

- service role וסיסמת MT5 ב-`.env` בלבד
- חיבור MT5 מדליק אוטומטית Algo Trading אם כבוי (Ctrl+E)
- ברירת מחדל `DRY_RUN=0` (מסחר חי); אפשר `DRY_RUN=1` רק לבדיקה בלי פקודות
- לא לפתוח אותו `id` פעמיים (`mt5_ticket` / claim)
- לא להשאיר שני טרמינלי MT5 פתוחים במקביל
- MAGIC נפרד מפריצה (260860 מול 260831); חזרה לפריצה רק דרך git history
- נוסחת 40/30 מ־`entry`+`tp` של הוובהוק; רצפת הקלאמפ היא `mt5_fill_price`

## כשמשפרים אסטרטגיה

1. לעדכן את הבלוק "מצב נוכחי" כאן
2. לעדכן את `STRATEGY.md` (כללים + שורת changelog עם התאריך)
3. לעדכן את `.cursor/rules/xaubot-strategy.mdc` אם כלל הביצוע השתנה
4. בהעלאה ל־VPS — ערך חדש בראש `VPS_UPDATES.md` (hook מזכיר ב־`git push`)
