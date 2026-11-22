# אסטרטגיית xaubot

עודכן לאחרונה: 2026-11-22

בוט העתקה: קורא איתותים מ-Supabase ופותח אותם בחשבון MT5 (מקומי או VPS).  
הבוט **לא** מייצר איתותים. האיתות נוצר במקור חיצוני (טבלת `wick_strategy` — אסטרטגיית ‎+60).  
MAGIC = `260860` (נפרד מפריצה `260831`).  
במקביל הוא מחשב כל יום מסחר רמות Gann (יומי / לונדון / NY) ושומר ב-`gann_levels` — בלי לפתוח מהן עסקאות.  
נעילת **40/30** מנוהלת **מקומית בבוט** בעמודות `bot_lock_*` — לא דרך `lock_state` של הוובהוק.  
אין פילטרי כניסה בבוט — `active_filters()` תמיד `[]` (סינון ב־Pine). קבצי `bot1_filters/` נשארים בדיסק ולא נטענים; `STRATEGY` ב־`.env` לא משפיע.

## מקור האיתות

| שדה | ערך |
|---|---|
| פרויקט Supabase | `pcieshmxxwplaodkrqet` |
| URL | `https://pcieshmxxwplaodkrqet.supabase.co` |
| טבלה | `public.wick_strategy` |
| MAGIC | `260860` |
| חיבור | `SUPABASE_SERVICE_ROLE_KEY` (RLS דולק בלי מדיניות) |

עמודות איתות: `direction` (`long`/`short`), `entry`, `stop`, `tp`, `status` (`open`/`closed`), `lot_mult` (ברירת מחדל 1, בין 0 ל־1 לא כולל 0).  
עמודות ביצוע: `mt5_ticket`, `copied_at`, `mt5_error`, `mt5_fill_price`.  
עמודות נעילה **של הבוט**: `bot_lock_state` (`none`/`pend`/`locked`), `bot_lock_sl`, `bot_lock_pend_bar_time`, `orig_stop`.  
עמודות נעילה **של הוובהוק/Pine** (יומן בלבד): `lock_state`, `lock_sl`, `lock_pend_bar_time`, `stop` — הבוט לא קורא אותן ל־`set_sl`.  
עמודות תוצאה MT5: `mt5_close_price`, `mt5_closed_at`, `mt5_profit`, `mt5_exit_reason` (יומן Pine נשאר ב־`exit` / `pips` / `usd_0_3`).

צבעי איתות בטבלה (`yellow` / `london` / `ny`) מתאימים לאינדיקטורי Gann בפרויקט.  
במצב wick הבוט **לא** חוסם `london` אחרי 15:30 — זה ב־Pine.
## מתי סורקים

כל `POLL_SEC` שניות (ברירת מחדל **5**). סריקה ראשונה מיד בהפעלה.

## כניסה (פתיחה ב-MT5)

איתות חדש = `status = open` **ו-** `mt5_ticket IS NULL`.

לפני פתיחה:
1. `active_filters()` — תמיד **רשימה ריקה**; סינון כניסה ב־Pine בלבד
2. גיל איתות ≤ `MAX_AGE_SEC` (ברירת מחדל 180)
3. סליפג' `|live - entry|` ≤ `min(MAX_SLIP_USD, MAX_SLIP_STOP_FRAC × |entry − stop|)` (ברירות מחדל 1.50 ו־0.20)
4. מספר פוזיציות פתוחות של הבוט (MAGIC נוכחי) < `MAX_OPEN` (ברירת מחדל **6**)
5. מרחק SL/TP ≥ `trade_stops_level` של הברוקר
6. אם `DRY_RUN=1` — רק כתיבת מה היה נפתח ל-`mt5_error`, בלי פקודה ל-MT5

פתיחה חיה (`DRY_RUN=0`):
1. claim על `copied_at` (רק אם עוד null) — מונע כפילות
2. `long` → BUY בשוק, `short` → SELL בשוק
3. סימבול מ-`MT5_SYMBOL`, נפח = `normalize_volume(LOT_SIZE × lot_mult)`. חסר / לא תקין / 0 → 1. תקרה 1, רצפה 0.05. `LOT_SIZE` ב־`.env` לא משתנה.
4. SL מ-`stop`, TP מ-`tp`
5. אחרי מילוי: `mt5_ticket`, `copied_at`, `mt5_fill_price` (לא דורסים את `entry` של היומן)
6. כשל: `mt5_error`, איפוס `copied_at` לניסיון חוזר

## פילטרים (`bot1_filters/`)

`active_filters()` מחזיר תמיד `[]` — אין חסימת כניסה בבוט (סינון ב־Pine).  
מודולי הפילטרים נשארים בתיקייה לשימוש עתידי / היסטוריה, ולא נטענים.

תיעוד/מעקב עסקאות נשאר ב־UTC; Gann לפי `Asia/Jerusalem`.

## נעילה 40/30 (מקומית בבוט, `bot_lock_*`)

הוובהוק עלול לכתוב `lock_state=locked` / לעדכן `stop` — **הבוט מתעלם** מזה ל־MT5. רק `bot_lock_*`.

בסיס לנוסחה: `entry` + `tp` **מהוובהוק** (לא `mt5_fill_price`).

1. `bot_lock_state=none` — בכל poll בודקים נרות **M1** מ־`entry_time + 5 דקות` (כמו Pine: הנר שאחרי הכניסה). אם high/low נגע ב־40% מ־entry→tp → באותו סבב מנסים `set_sl` ל־30% (`LOCK_SL_FRAC`, ברירת מחדל 0.30).
2. לפני `set_sl`: אם המחיר כבר עבר את רמת ה־30% (לונג `bid ≤ lockSL`, שורט `ask ≥ lockSL`) — **סגירה בשוק** כ־lock (`bot_lock_state=locked`, `mt5_exit_reason=lock`). אין "דילוג ונשארים none" במקרה הזה.
3. אם המחיר **עדיין** בצד הנכון של ה־30% אבל הברוקר דוחה רק בגלל `min_dist` — נשארים `none` ומנסים בסבב הבא. קלאמפ ≥ `min_stop_distance × 1.2` ולא חוצה `mt5_fill_price` כשאפשר להציב SL.
4. הצלחת `set_sl` → `bot_lock_state=locked`, `bot_lock_sl=…`. **לא** כותבים `stop` בטבלה (הוובהוק עלול לדרוס `stop`; הבוט משתמש ב־`orig_stop` / `bot_lock_sl`).
5. `locked` — אין שינוי נוסף; TP נשאר קבוע.
6. גיבוי: אם Pine כבר `status=closed` והפוזיציה עדיין פתוחה ב־MT5 מעל 10 שניות → `PINE_EXIT_SYNC` סוגר בשוק.

בעליית הבוט נרשם ללוג `stops_level` של הסימבול (points ו־$).

סיווג סגירה ב־`mt5_exit_reason`: לפי מחיר מול `orig_stop` / `tp` / `bot_lock_sl`.  
באותו נר יעד+סטופ → בפיין סטופ קודם; ב־MT5 מי שנוגע קודם בזמן.

`LOCK_ENABLE=1` (ברירת מחדל). ב־`DRY_RUN` הנעילה מסומנת ב־`bot_lock_*` בלי `set_sl` אמיתי.

## סגירה (מ-MT5 חזרה ל-Supabase)

מעקב הבוט: `mt5_ticket` לא ריק, `mt5_closed_at` ריק, ו־`date_idt` ב־7 הימים האחרונים (שעון ישראל). לא לפי `status`.

כל סריקה, לכל שורה כזו:
- פתוחה ב-MT5 → עדכון `mt5_profit` רק אם השתנה (סף $0.01). בלי לגעת ב־`stop` / `tp` / `usd_0_3`
- נסגרה ב-MT5 → רק אם יש OUT deal עם `position_id` = הטיקט. נכתבים `mt5_close_price`, `mt5_closed_at` (UTC), `mt5_profit`, `mt5_exit_reason`
- `deal.time` הוא שעון שרת הברוקר. ממירים ל-UTC לפי הפרש שעות שלמות מול `tick.time`. טיק ישן (סוף שבוע) משתמש בהפרש האחרון שנשמר
- הבוט **לא** כותב `status`, `exit`, `exit_reason`, `pips`, `usd_0_3` — אלה של ה-Pine
- בלי פוזיציה ובלי OUT deal → לא נכתב `mt5_closed_at`
- יתום: פוזיציית MAGIC בלי שורה במעקב → לוג `ORPHAN` בלבד

סגירה רגילה מגיעה מ-SL/TP ב-MT5 (או ידני). חריגים יזומים של הבוט: (1) נעילה כשהמחיר כבר עבר את ה־30% → סגירה בשוק כ־lock; (2) `PINE_EXIT_SYNC` כשהיומן closed והפוזיציה עדיין פתוחה.  
בהתחברות/סריקה: אם Algo Trading כבוי — הבוט מדליק אותו אוטומטית.

### נקודת חיתוך — 2026-09-21

לפני התאריך הזה, בשורות עם `mt5_ticket`, השדות `exit` / `exit_reason` משקפים את MT5 (הבוט וה-resync דרסו את מה שה-Pine שלח). אי אפשר לשחזר את ערכי ה-Pine המקוריים שלהן.  
מהתאריך הזה: `exit` / `exit_reason` הם של ה-Pine. הביצוע בברוקר נמצא ב־`mt5_exit_reason`, `mt5_profit`, `mt5_closed_at`.  
שורות בלי `mt5_ticket` תמיד היו ותמיד יישארו של ה-Pine.

## Heartbeat + watchdog

אחרי כל סבב מוצלח (`run_cycle`) הבוט כותב `heartbeat.txt` בתיקיית הפרויקט (חותמת UTC).  
`watchdog.ps1` בודק כל ~30 שניות: אם אין תהליך / הדופק ישן מ־90 שניות — מפעיל MT5 (אם צריך) ו־`python bot.py` מחדש.  
`$BotDir` = תיקיית הסקריפט (`$PSScriptRoot`).  
`$EntryScript` = נתיב **מלא** ל־`bot.py` (לא יחסי).  
`Start-Bot` מפנה stdout/stderr ל־`bot_out.log` / `bot_err.log` (ברירת מחדל בריפו).  
Python: `WATCHDOG_PYTHON` ב־`.env`, אחרת `.venv\Scripts\python.exe`.  
תיקונים מקומיים ל־VPS בלי להידרס ב־pull: `watchdog.local.ps1` — **רק משתנים** (`$Python`, `$StaleSec`).  
סדר בטוח: קודם לוודא ש־`heartbeat.txt` מתעדכן, ורק אז `$StaleSec = 180` ב־local.  
על VPS: משימת Scheduler אחת ב־**AtLogOn** בלבד.


## רמות Gann אוטומטיות

טבלה: `public.gann_levels` (ייחודי לפי `date_idt` + `session`).  
נוסחאות זהות ל-`gan.pine` / `londongan.pine` / `NYgan.pine`. תאריך מסחר לפי `Asia/Jerusalem`.  
הרמות **לא** פותחות עסקה.

## סטטיסטיקה יומית (`gold_statics`)

מודול [`statics.py`](statics.py). אחרי **00:05** שעון ישראל מסכם את **אתמול** (`date_idt`) מ־`wick_strategy` וכותב שורה אחת ל־`gold_statics`:

| שדה | משמעות |
|---|---|
| `trades_total` / `trades_closed` / `trades_open` | כמה עסקאות ביום |
| `count_tp` / `count_sl` / `count_lock` | כמה נסגרו ביעד / סטופ / נעילה |
| `count_other` | יציאות אחרות (manual וכו') |
| `profit_usd` | סכום `usd_0_3` (או `mt5_profit`) |
| `pips_total` | סכום פיפס |

פעם אחת ליום (upsert לפי `date_idt`). לא פותח עסקאות.

## מה האסטרטגיה עדיין לא עושה

- אין trailing רציף (רק קפיצת נעילה חד־פעמית ל־30%)
- אין חישוב לוט לפי סיכון
- סינון חדשות/שעות ב־Pine (במצב wick); לא בבוט
- רץ על Windows עם טרמינל MT5 פתוח (מומלץ VPS)

## הפעלה

```powershell
pip install -r requirements.txt
copy .env.example .env
```

ב-Supabase ל־wick: `sql/wick_bot_lock_columns.sql` (עמודות `bot_lock_*`).

ב-`.env`: Supabase + MT5. ברירת מחדל `DRY_RUN=0` (מסחר חי).

```powershell
# 1) Smoke: DRY_RUN — expect log to find wick_strategy rows and mt5_error "DRY_RUN would …"
#    Also confirm no leftover MAGIC 260831 breakout positions in MT5.
$env:DRY_RUN=1
python bot.py
# 2) Live
$env:DRY_RUN=0
python bot.py
```

## היסטוריית שינויים

יומן העלאות מפורט ל־VPS (תאריך + מה השתנה): [VPS_UPDATES.md](VPS_UPDATES.md).  
⭐ חשוב להשוואה: cutover ל־wick 40/30 + `bot_lock_*` (2026-11-22); וגם 50/30→40/35 (2026-09-12).

- **2026-11-22** — `lot_mult`: הוובהוק שומר את השדה בכניסה (חסר → 1, אחרת תקרה 1 ורצפה 0.05). הבוט פותח `LOT_SIZE × lot_mult`. בלי השדה הלוט נשאר מלא. נעילה, סנכרון ו־`active_filters()` לא השתנו.
- **2026-11-22** — Cutover ל־`wick_strategy` (+60): `TABLE=wick_strategy`, `MAGIC=260860`, נעילה 40/30 מיידית על M1 מ־`entry_time+5m` בעמודות `bot_lock_*` (בלי לכתוב `stop`); אם המחיר כבר עבר את ה־30% → סגירה בשוק כ־lock; `PINE_EXIT_SYNC`; `MAX_OPEN=6`. `active_filters()` תמיד `[]` (לא תלוי ב־`STRATEGY`). סליפג׳: `min(MAX_SLIP_USD, MAX_SLIP_STOP_FRAC×|entry−stop|)` (ברירות 1.5 / 0.20). לוג עלייה: `wick table=… magic=… filters=[]`.
- **2026-11-22** — M1 rates: חלון שעון־שרת + `tzinfo=UTC` ל־`copy_rates_range` (לא naive); `bar.time` כ־UTC. לוג `scan_from_utc` / `first_bar_utc` / `last_bar_utc`. Clamp ל־fill רק אם fill לא עבר את raw_sl. `test_open_trade` MAGIC=260860.
- **2026-11-15 20:10 IDT** — תיקייה `filters/` → `bot1_filters/`. נוספו E `ny_midday`, F `one_per_bar`, G `min_rr` + `_mode.py`; context ב־`bot.py` (`created_at`, `same_bar_open`). ברירות: E/F=`shadow`, G=`off`.
- **2026-11-07 18:52 IDT** — `h1_ema_side` חובר ל־`active_filters()` (אחרון בשרשרת). ברירת מחדל `H1_EMA_MODE=shadow` (לוג בלבד); לחסימה אמיתית: `H1_EMA_MODE=block` ב־`.env`.
- **2026-11-07 18:34 IDT** — נוסף `filters/h1_ema_side.py` (EMA50 של H1, כניסה רק נגד הצד של המחיר). בסיס בקטסט 14/09–06/11: 762 עסקאות ‎−$2,564.53 → 313 מאושרות ‎+$1,886.14; הקבוצה שנחסמת הפסידה ב־8/8 שבועות.
- **2026-10-20** — `us_holiday_next_day`: לוח NYSE מלא בלי Columbus/Veterans; זיהוי לפי NY; `mt5_error` עם שם החג.
- **2026-09-21** — הפרדת יומן/ביצוע: הבוט עוקב 7 ימים לפי `mt5_ticket` + `mt5_closed_at` (לא `status`); כותב `mt5_exit_reason` ולא נוגע ב־`exit`/`exit_reason`/`usd_0_3`. נעילה: דילוג לפני קלאמפ אם SL בצד הלא נכון; קלאמפ לא חוצה `mt5_fill_price`. `deal.time` → UTC לפי offset שעתי, עם cache כשהטיק ישן.
- **2026-09-20** — Watchdog: `$EntryScript` נתיב מלא; redirect ל־`bot_out.log`/`bot_err.log` ב־Start-Bot; `watchdog.local.ps1` רק למשתנים.
- **2026-09-19** — נעילה: `normalize_price` (point grid) לפני set_sl; דילוג אם SL בצד הלא נכון של השוק; לוג `sent_sl`+`retcode`; רצפת stops×1.2.
- **2026-09-19** — סנכרון סגירות: `history_deals_get(position=ticket)` אחרי טעינת טווח היסטוריה; בלי `order==ticket`; לא לסמן closed בלי OUT; ORPHAN בלוג; מיפוי DEAL_REASON תוקן (4=sl,5=tp); `resolve_exit_reason` לפי מחיר מול stop/tp/lock.
- **2026-09-13** — Watchdog: `$PSScriptRoot`, `WATCHDOG_PYTHON` / `watchdog.local.ps1`; `.gitignore` ל־`heartbeat.txt` / `*.bak` / `*.log`.
- **2026-09-13** — Heartbeat: אחרי כל `run_cycle` נכתב `heartbeat.txt`; `watchdog.ps1` ל־VPS (`C:\bots\xauusd`).
- **2026-09-13** — פחות כתיבות לסופבייס: PATCH לפתוחות רק אם profit/SL/TP השתנו; cache ל־Gann/statics אחרי שמירה.
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
