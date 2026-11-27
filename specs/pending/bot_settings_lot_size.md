# מפרט: שינוי לוט מ־Supabase בלי VPS (`bot_settings`)

**תאריך:** 22.11.2026
**סטטוס:** ⏸️ שמור לביצוע מאוחר יותר. לא לממש עכשיו.
**מטרה:** לשנות את הלוט הבסיסי של הבוט מהדפדפן או מהטלפון, בלי להיכנס ל־VPS ובלי להפעיל את הבוט מחדש.
**קבצים:** SQL חדש, `bot.py`, מודול חדש `bot_settings.py`, ‏`.env.example`, תיעוד.

---

## 1. היום ומה ישתנה

| | היום | אחרי |
|---|---|---|
| איפה הלוט | `LOT_SIZE` ב־`.env` ב־VPS | שורה בטבלה `bot_settings` ב־Supabase |
| איך משנים | עורכים `.env` ומפעילים את הבוט מחדש | Table Editor ← משנים מספר ← Save |
| מתי נכנס לתוקף | אחרי הפעלה מחדש | בעסקה הבאה, תוך עד 60 שניות |
| לוט בפועל | `LOT_SIZE × lot_mult` | `lot_size (מהטבלה) × lot_mult` |

`lot_mult` (‏1, ‏0.5 או 0.25) ממשיך להגיע מה־Pine בכל התראה, בלי שינוי.

---

## 2. SQL (אתה מריץ)

```sql
create table if not exists public.bot_settings (
  key         text primary key,
  value       numeric not null,
  note        text,
  updated_at  timestamptz not null default now()
);

-- ערכים התחלתיים: לשים את מה שיש היום ב־.env
insert into public.bot_settings (key, value, note) values
  ('lot_size', 0.30, 'בסיס לוט. לוט בפועל = lot_size × lot_mult'),
  ('max_lot',  0.60, 'תקרת ביטחון: lot_size גבוה מזה לא ייכנס לשימוש')
on conflict (key) do nothing;

-- updated_at מתעדכן אוטומטית בכל עריכה
create or replace function public.bot_settings_touch() returns trigger
language plpgsql as $$ begin new.updated_at := now(); return new; end $$;
drop trigger if exists trg_bot_settings_touch on public.bot_settings;
create trigger trg_bot_settings_touch before update on public.bot_settings
  for each row execute function public.bot_settings_touch();

-- אבטחה: RLS פעיל ובלי policies. רק ה־service role (הבוט) וה־dashboard ניגשים.
alter table public.bot_settings enable row level security;
```

> לפני ההרצה, לבדוק מה ה־`LOT_SIZE` ב־`.env` של ה־VPS, ולשים את אותו מספר ב־`lot_size`. כך המעבר לא ישנה את הלוט.

---

## 3. הבוט: מודול חדש `bot_settings.py`

### 3.1 קריאה ושמירה בזיכרון
- `get_lot_size(db) -> float`, שנקרא לפני כל פתיחת עסקה.
- **cache של 60 שניות** (`BOT_SETTINGS_TTL_SEC`, ברירת מחדל 60). בתוך החלון לא פונים ל־DB שוב, כדי לא להוסיף SELECT בכל poll של 5 שניות.
- קורא את שתי השורות `lot_size` ו־`max_lot` בשאילתה אחת: `select key, value from bot_settings where key in (...)`.

### 3.2 בדיקת תקינות
הערך נכנס לשימוש רק אם **כל** התנאים מתקיימים:
- מספר תקין, לא NaN.
- `lot_size > 0`.
- `lot_size <= max_lot`. אם `max_lot` חסר, התקרה היא `MAX_LOT_ENV` מ־`.env` (ברירת מחדל 1.0).
- `lot_size >= volume_min` של הסימבול ב־MT5.

ערך לא תקין: לא משתמשים בו, ממשיכים עם **האחרון התקין**, ורושמים WARNING פעם אחת עד שהערך ישתנה:
`bot_settings: lot_size=5.0 rejected (> max_lot 0.6) — keeping 0.3`

### 3.3 גיבוי
סדר עדיפות:
1. ערך תקין מהטבלה.
2. הערך התקין האחרון שנקרא, אם הטבלה לא זמינה או שיש שגיאת רשת.
3. `LOT_SIZE` מ־`.env`, אם עוד לא נקרא שום ערך מאז שהבוט עלה.

תקלה ב־Supabase **לא עוצרת** את הבוט ולא פותחת עסקה בלוט לא ידוע.

### 3.4 לוג
- **בעלייה:** `bot_settings: lot_size=0.3 max_lot=0.6 (source=table)`, או `(source=.env fallback)`.
- **בכל שינוי:** `LOT_SIZE changed 0.3 → 0.5 (bot_settings, updated_at=…)`.
- **בפתיחת עסקה**, כמו היום: `lot=0.25 (lot_size=0.5 × lot_mult=0.5)`.

### 3.5 חיבור ל־`bot.py`
במקום `lot_size = env_float("LOT_SIZE", 0.01)` בפתיחת עסקה:
```python
lot_size = bot_settings.get_lot_size(db)          # table → last good → .env
mult = lot_mult(trade.get("lot_mult"))
volume = mt5.normalize_volume(lot_size * mult, symbol)
```
`DRY_RUN` מתנהג אותו דבר: הלוט שנכתב ב־`mt5_error` ("DRY_RUN would …") מחושב מהטבלה.

---

## 4. `.env.example` ותיעוד

```
# Base lot now comes from Supabase table bot_settings (key=lot_size).
# LOT_SIZE below is only the fallback if the table is unreachable at startup.
LOT_SIZE=0.30
MAX_LOT_ENV=1.0
BOT_SETTINGS_TTL_SEC=60
```
- `STRATEGY.md` / `MEMORY.md`: הלוט מגיע מ־`bot_settings`, ‏`.env` הוא רק גיבוי.
- `VPS_UPDATES.md`: רשומה על השינוי.

---

## 5. איך משנים לוט אחרי המימוש

1. Supabase ← Table Editor ← `bot_settings`.
2. בשורה `lot_size` משנים את `value`, למשל ל־0.5, ושומרים.
3. תוך עד 60 שניות מופיע בלוג `LOT_SIZE changed 0.3 → 0.5`.
4. העסקה הבאה נפתחת ב־`0.5 × lot_mult`: ‏0.50, ‏0.25 או 0.12.

**להעלאה מעל התקרה:** קודם מעלים את `max_lot`, ורק אז את `lot_size`. זו הגנה מפני טעות הקלדה, למשל 5 במקום 0.5.

---

## 6. בדיקות קבלה (DRY_RUN=1 ואז דמו)

| # | בדיקה | צפוי |
|---|---|---|
| T1 | עלייה עם `lot_size=0.3` | לוג `source=table`, ועסקה עם `lot_mult=1` נפתחת ב־0.3 |
| T2 | משנים בטבלה ל־0.5 בזמן ריצה | תוך עד 60 שניות `LOT_SIZE changed 0.3 → 0.5`, בלי הפעלה מחדש |
| T3 | `lot_size=5` (מעל `max_lot`) | WARNING, נשאר 0.5 |
| T4 | `lot_size=0` או ערך שלילי | WARNING, נשאר האחרון התקין |
| T5 | ניתוק רשת ל־Supabase אחרי שכבר נקרא ערך | ממשיך עם האחרון התקין, הבוט לא קורס |
| T6 | הטבלה לא קיימת או לא זמינה בעלייה | לוג `source=.env fallback`, לוט מ־`LOT_SIZE` |
| T7 | `lot_mult=0.25` עם `lot_size=0.5` | ‏`lot=0.12` |
| T8 | ספירת SELECT ל־`bot_settings` | לכל היותר אחת לדקה, לא בכל poll |

---

## 7. אופציות להמשך (לא בשלב הזה)
- `trading_enabled` (0/1): לעצור פתיחת עסקאות חדשות מהטלפון. עסקאות פתוחות ממשיכות להיות מנוהלות.
- `max_open`: להחליף את `MAX_OPEN` מה־`.env`.
- `daily_loss_stop_usd`: עצירת מסחר אחרי הפסד יומי מסוים, הגנה ל־FTMO.
