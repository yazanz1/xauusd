# אסטרטגיית xaubot

עודכן לאחרונה: 2026-09-12

בוט העתקה: קורא איתותים מ-Supabase ופותח אותם בחשבון MT5 (מקומי או VPS).  
הבוט **לא** מייצר איתותים. האיתות נוצר במקור חיצוני (טבלת `gold_trades`).  
במקביל הוא מחשב כל יום מסחר רמות Gann (יומי / לונדון / NY) ושומר ב-`gann_levels` — בלי לפתוח מהן עסקאות.  
נעילת 40/35 מנוהלת **מקומית בבוט** — לא דרך `action=modify` מסופבייס.
פילטרים ב־`filters/` — כל פילטר בקובץ משלו; רצים לפני פתיחה ב־MT5.

## מקור האיתות

| שדה | ערך |
|---|---|
| פרויקט Supabase | `pcieshmxxwplaodkrqet` |
| URL | `https://pcieshmxxwplaodkrqet.supabase.co` |
| טבלה | `public.gold_trades` |
| חיבור | `SUPABASE_SERVICE_ROLE_KEY` (RLS דולק בלי מדיניות) |

עמודות איתות: `direction` (`long`/`short`), `entry`, `stop`, `tp`, `status` (`open`/`closed`).  
עמודות ביצוע: `mt5_ticket`, `copied_at`, `mt5_error`, `mt5_fill_price`.  
עמודות נעילה: `lock_state` (`none`/`pend`/`locked`), `lock_sl`, `lock_pend_bar_time`, `orig_stop`.  
עמודות תוצאה MT5: `mt5_close_price`, `mt5_closed_at`, `mt5_profit` (+ גם `exit` / `pips` / `usd_0_3` לתאימות).

צבעי איתות בטבלה (`yellow` / `london` / `ny`) מתאימים לאינדיקטורי Gann בפרויקט.  
אחרי `15:30` שעון ישראל הבוט חוסם כניסות `london`; מותרות `yellow` ו־`ny`.

## מתי סורקים

כל `POLL_SEC` שניות (ברירת מחדל **5**). סריקה ראשונה מיד בהפעלה.

## כניסה (פתיחה ב-MT5)

איתות חדש = `status = open` **ו-** `mt5_ticket IS NULL`.

לפני פתיחה:
1. פילטר שעות ישראל (`filters/israel_hours.py`): אין כניסות ב־`12:00–14:30` ו־`22:00–00:00` (Asia/Jerusalem, חצי־פתוח)
2. פילטר לונדון אחרי NY (`filters/london_after_ny.py`): מ־`15:30` שעון ישראל אין כניסות `color=london` (רק `yellow` / `ny`)
3. פילטר יום אחרי חג US (`filters/us_holiday_next_day.py`): ביום המסחר שאחרי חג — כניסות רק עד `10:00` IDT
4. פילטר פתיחת NY (`filters/ny_open.py`): אין כניסות ב־`15:20–15:30` IDT בכל יום
5. גיל איתות ≤ `MAX_AGE_SEC` (ברירת מחדל 180)
6. סליפג' `|live - entry|` ≤ `MAX_SLIP_USD` (ברירת מחדל 1.50)
7. מספר פוזיציות פתוחות של הבוט < `MAX_OPEN` (ברירת מחדל 2)
8. מרחק SL/TP ≥ `trade_stops_level` של הברוקר
9. אם `DRY_RUN=1` — רק כתיבת מה היה נפתח ל-`mt5_error`, בלי פקודה ל-MT5

פתיחה חיה (`DRY_RUN=0`):
1. claim על `copied_at` (רק אם עוד null) — מונע כפילות
2. `long` → BUY בשוק, `short` → SELL בשוק
3. סימבול מ-`MT5_SYMBOL`, נפח מ-`LOT_SIZE`
4. SL מ-`stop`, TP מ-`tp`
5. אחרי מילוי: `mt5_ticket`, `copied_at`, `mt5_fill_price` (לא דורסים את `entry` של היומן)
6. כשל: `mt5_error`, איפוס `copied_at` לניסיון חוזר

## פילטרים (`filters/`)

כל פילטר = קובץ נפרד. השרשרת מ־`active_filters()` רצה לפני פתיחה; דחייה נכתבת ל־`mt5_error` בלי claim.

| פילטר | כלל |
|---|---|
| `israel_hours` | אין כניסות חדשות ב־`[12:00,14:30)` ו־`[22:00,24:00)` שעון ישראל |
| `london_after_ny` | מ־`15:30` שעון ישראל חוסמים `london`; מותרים `yellow` ו־`ny` |
| `us_holiday_next_day` | ביום המסחר שאחרי חג US (observed): כניסות רק עד `10:00` IDT; מ־`10:00` אין כניסה חדשה |
| `ny_open` | בכל יום אין כניסות ב־`[15:20,15:30)` IDT (פתיחת NY / נזילות שקרית) |

חגי US בפילטר: New Year, MLK, Presidents' Day, Good Friday, Memorial Day, Juneteenth, Independence Day, Labor Day, Columbus/Indigenous Peoples' Day, Veterans Day, Thanksgiving, Christmas.  
חג בסופ״ש → observed (שבת→שישי, ראשון→שני); הפילטר על יום המסחר הבא אחרי ה־observed. פתוחות ממשיכות.

תיעוד/מעקב עסקאות נשאר ב־UTC; רק חלונות הפילטר וה־Gann לפי `Asia/Jerusalem`.

## נעילה 40/35 (מקומית בבוט)

**לא** מסתמכים על UPDATE של `stop` מסופבייס באמצע.

בסיס לנוסחה: `entry` + `tp` **מהוובהוק** (לא `mt5_fill_price`) — דיוק מול היומן.

1. `none` — על נר M5 **סגור**: אם נגע ב־40% מהמרחק entry→tp  
   - לונג: `high >= entry + 0.4*(tp-entry)`  
   - שורט: `low <= entry - 0.4*(entry-tp)`  
   → `pend`, `lock_sl = entry ± 0.35*dist`, שמירת `lock_pend_bar_time`
2. `pend` — ב־POLL הראשון אחרי ש**התחיל** נר M5 הבא → `set_sl(lockSL)` ב־MT5 → `locked`, עדכון `stop` בסופבייס
3. `locked` — אין שינוי נוסף; TP נשאר קבוע

סגירה (נגיעת hi/lo):
- `exit_reason=tp` — נגיעה ביעד
- `exit_reason=sl` — סטופ מקורי לפני שננעל
- `exit_reason=lock` — סטופ אחרי נעילה; `pips` מחושב מ־`entry` (webhook) ליציאה
- באותו נר יעד+סטופ → בפיין סטופ קודם; ב־MT5 מי שנוגע קודם בזמן

`LOCK_ENABLE=1` (ברירת מחדל). ב־`DRY_RUN` הנעילה מסומנת ב־DB בלי `set_sl` אמיתי.

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

## סטטיסטיקה יומית (`gold_statics`)

מודול [`statics.py`](statics.py). אחרי **00:05** שעון ישראל מסכם את **אתמול** (`date_idt`) מ־`gold_trades` וכותב שורה אחת ל־`gold_statics`:

| שדה | משמעות |
|---|---|
| `trades_total` / `trades_closed` / `trades_open` | כמה עסקאות ביום |
| `count_tp` / `count_sl` / `count_lock` | כמה נסגרו ביעד / סטופ / נעילה |
| `count_other` | יציאות אחרות (manual וכו') |
| `profit_usd` | סכום `usd_0_3` (או `mt5_profit`) |
| `pips_total` | סכום פיפס |

פעם אחת ליום (upsert לפי `date_idt`). לא פותח עסקאות.

## מה האסטרטגיה עדיין לא עושה

- אין trailing רציף (רק קפיצת נעילה חד־פעמית ל־35%)
- אין חישוב לוט לפי סיכון
- אין סינון לפי חדשות
- רץ על Windows עם טרמינל MT5 פתוח (מומלץ VPS)

## הפעלה

```powershell
pip install -r requirements.txt
copy .env.example .env
```

ב-Supabase להריץ פעם אחת אם חסר: `sql/mt5_bridge_columns.sql` ו־`sql/lock_columns.sql`.

ב-`.env`: Supabase + MT5. ברירת מחדל `DRY_RUN=0` (מסחר חי).

```powershell
python bot.py
```

## היסטוריית שינויים

- **2026-09-13** — סטטיסטיקה יומית ל־`gold_statics` (`statics.py`): tp/sl/lock + רווח בסוף היום.
- **2026-09-13** — ברירת מחדל `DRY_RUN=0` (מסחר חי ופתיחת עסקאות).
- **2026-09-12** — נעילה עודכנה ל־40/35 (נגיעה ב־40% → SL ל־35% בנר הבא).
- **2026-09-13** — `israel_hours`: חלון צהריים עד `14:30` (במקום 14:00).
- **2026-09-12** — פילטר `ny_open`: אין כניסות ב־15:20–15:30 IDT בכל יום.
- **2026-09-12** — פילטר `us_holiday_next_day`: יום מסחר אחרי חג US — כניסות רק עד 10:00 IDT.
- **2026-09-12** — פילטר `london_after_ny`: מ־15:30 ישראל חוסמים כניסות `london` (מותרים yellow/ny).
- **2026-09-12** — פילטר `israel_hours`: אין כניסות ב־12:00–14:00 ו־22:00–00:00 (Asia/Jerusalem); מודול `filters/`.
- **2026-09-12** — נעילת 50/30 מקומית בבוט (`none→pend→locked`); `exit_reason=lock` + `pips`; עמודות `lock_*` / `orig_stop`.
- **2026-09-08** — סריקת poll מהירה; הגנות age/slip/max_open/dry-run/claim; עמודות `mt5_*` למילוי/סגירה בלי לדרוס entry של היומן.
- **2026-09-02** — סשן MT5 ב-`sessions/`; reconnect אוטומטי.
- **2026-09-01** — חיבור דרך פרטי `.env` / טרמינל.
- **2026-08-31** — Gann levels + גרסה ראשונה של העתקה.
