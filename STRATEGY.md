# אסטרטגיית xaubot

עודכן לאחרונה: 2026-09-08

בוט העתקה: קורא איתותים מ-Supabase ופותח אותם בחשבון MT5 (מקומי או VPS).  
הבוט **לא** מייצר איתותים. האיתות נוצר במקור חיצוני (טבלת `gold_trades`).  
במקביל הוא מחשב כל יום מסחר רמות Gann (יומי / לונדון / NY) ושומר ב-`gann_levels` — בלי לפתוח מהן עסקאות.

## מקור האיתות

| שדה | ערך |
|---|---|
| פרויקט Supabase | `pcieshmxxwplaodkrqet` |
| URL | `https://pcieshmxxwplaodkrqet.supabase.co` |
| טבלה | `public.gold_trades` |
| חיבור | `SUPABASE_SERVICE_ROLE_KEY` (RLS דולק בלי מדיניות) |

עמודות איתות: `direction` (`long`/`short`), `entry`, `stop`, `tp`, `status` (`open`/`closed`).  
עמודות ביצוע: `mt5_ticket`, `copied_at`, `mt5_error`, `mt5_fill_price`.  
עמודות תוצאה MT5: `mt5_close_price`, `mt5_closed_at`, `mt5_profit` (+ גם `exit` / `pips` / `usd_0_3` לתאימות).

צבעי איתות בטבלה (`yellow` / `london` / `ny` / `white`) מתאימים לאינדיקטורי Gann בפרויקט. הבוט **לא** מסנן לפי צבע כרגע.

## מתי סורקים

כל `POLL_SEC` שניות (ברירת מחדל **5**). סריקה ראשונה מיד בהפעלה.

## כניסה (פתיחה ב-MT5)

איתות חדש = `status = open` **ו-** `mt5_ticket IS NULL`.

לפני פתיחה:
1. גיל איתות ≤ `MAX_AGE_SEC` (ברירת מחדל 180)
2. סליפג' `|live - entry|` ≤ `MAX_SLIP_USD` (ברירת מחדל 1.50)
3. מספר פוזיציות פתוחות של הבוט < `MAX_OPEN` (ברירת מחדל 2)
4. מרחק SL/TP ≥ `trade_stops_level` של הברוקר
5. אם `DRY_RUN=1` — רק כתיבת מה היה נפתח ל-`mt5_error`, בלי פקודה ל-MT5

פתיחה חיה (`DRY_RUN=0`):
1. claim על `copied_at` (רק אם עוד null) — מונע כפילות
2. `long` → BUY בשוק, `short` → SELL בשוק
3. סימבול מ-`MT5_SYMBOL`, נפח מ-`LOT_SIZE`
4. SL מ-`stop`, TP מ-`tp`
5. אחרי מילוי: `mt5_ticket`, `copied_at`, `mt5_fill_price` (לא דורסים את `entry` של היומן)
6. כשל: `mt5_error`, איפוס `copied_at` לניסיון חוזר

## סגירה (מ-MT5 חזרה ל-Supabase)

כל סריקה, לכל שורה `open` עם ticket:
- פתוחה ב-MT5 → עדכון `mt5_profit` (רווח צף) + סנכרון SL/TP
- נסגרה ב-MT5 (SL/TP/ידני) → `status=closed`, `mt5_close_price`, `mt5_closed_at`, `mt5_profit`, וגם `exit` / `exit_time` / `exit_reason` / `pips` / `usd_0_3`

הבוט **לא** סוגר יזום — הסגירה מגיעה מ-SL/TP ב-MT5 (או ידני בטרמינל).  
בהתחברות/סריקה: אם Algo Trading כבוי — הבוט מדליק אותו אוטומטית.

## רמות Gann אוטומטיות

טבלה: `public.gann_levels` (ייחודי לפי `date_idt` + `session`).  
נוסחאות זהות ל-`gan.pine` / `londongan.pine` / `NYgan.pine`. תאריך מסחר לפי `Asia/Jerusalem`.  
הרמות **לא** פותחות עסקה.

## מה האסטרטגיה עדיין לא עושה

- אין trailing / lock / breakeven אוטומטי מהבוט
- אין חישוב לוט לפי סיכון
- אין סינון לפי צבע / חדשות
- רץ על Windows עם טרמינל MT5 פתוח (מומלץ VPS)

## הפעלה

```powershell
pip install -r requirements.txt
copy .env.example .env
```

ב-Supabase להריץ פעם אחת: `sql/mt5_bridge_columns.sql`.

ב-`.env`: Supabase + MT5 + `DRY_RUN=1` בהתחלה.

```powershell
python bot.py
```

## היסטוריית שינויים

- **2026-09-08** — סריקת poll מהירה; הגנות age/slip/max_open/dry-run/claim; עמודות `mt5_*` למילוי/סגירה בלי לדרוס entry של היומן.
- **2026-09-02** — סשן MT5 ב-`sessions/`; reconnect אוטומטי.
- **2026-09-01** — חיבור דרך פרטי `.env` / טרמינל.
- **2026-08-31** — Gann levels + גרסה ראשונה של העתקה.
