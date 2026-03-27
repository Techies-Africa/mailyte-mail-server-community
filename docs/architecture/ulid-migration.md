# ULID Migration Plan — Email Server + Laravel API

## What is ULID?

ULID (Universally Unique Lexicographically Sortable Identifier) is a 26-character string that replaces auto-increment integers and UUIDs as primary keys.

```
01ARZ3NDEKTSV4RRFFQ69G5FAV
└──────┘└──────────────────┘
 time        randomness
(48 bit)    (80 bit)
```

**Why ULID over INT or UUID:**

| Feature | INT AUTO_INCREMENT | UUID v4 | ULID |
|---------|-------------------|---------|------|
| Globally unique | No (per-table only) | Yes | Yes |
| Sortable by time | Yes | No | Yes |
| Safe for distributed DBs | No | Yes | Yes |
| URL-safe | Yes | No (hyphens) | Yes |
| Index performance | Best | Worst (random) | Good (sorted) |
| Collision at scale | Impossible (sequential) | Near-zero | Near-zero |
| Exposes creation order | Yes (security risk) | No | Partially (ms precision) |
| Storage | 4 bytes (INT) / 8 bytes (BIGINT) | 36 chars | 26 chars |

For 500K+ domains with millions of mailboxes across potentially distributed infrastructure, ULID is the right choice.

---

## Email Server Migration (mailyte-email-server)

### Status: In Progress

**Migration file:** `database/migrations/sql/009_ulid_primary_keys.sql`
**Utility:** `shared/ulid_utils.py`

### Tables Being Migrated

| Table | Old PK Type | New PK Type | FK Columns Affected |
|-------|------------|-------------|-------------------|
| organizations | VARCHAR(100) | CHAR(26) | domains.organization_id, email_accounts.organization_id, aliases.organization_id, api_keys.organization_id, transport_rules.organization_id |
| domains | INT | CHAR(26) | email_accounts.domain_id, aliases.domain_id, dkim_keys.domain_id, email_tracking.domain_id |
| email_accounts | INT | CHAR(26) | user_sessions.email_account_id |
| aliases | INT | CHAR(26) | — |
| api_keys | INT | CHAR(26) | — |
| dkim_keys | INT | CHAR(26) | — |
| email_tracking | BIGINT | CHAR(26) | — |
| mail_logs | INT | CHAR(26) | — |
| audit_logs | INT | CHAR(26) | — |
| webhook_delivery_logs | INT | CHAR(26) | — |
| ssl_certificates | INT | CHAR(26) | — |
| transport_rules | INT | CHAR(26) | — |
| quarantine | INT | CHAR(26) | — |
| health_checks | INT | CHAR(26) | — |
| service_metrics | INT | CHAR(26) | — |
| url_clicks | INT | CHAR(26) | — |
| tracking_statistics | INT | CHAR(26) | — |
| email_suppressions | INT | CHAR(26) | — |
| user_sessions | INT | CHAR(26) | — |
| migration_jobs | INT | CHAR(26) | — |
| webhook_urls | INT | CHAR(26) | — |

### ULID Generation in Python

```python
# shared/ulid_utils.py
from ulid import ULID

def generate_ulid() -> str:
    return str(ULID())
```

### SQLAlchemy Model Pattern

```python
from sqlalchemy import Column, String, ForeignKey
from shared.ulid_utils import generate_ulid

class Domain(Base):
    __tablename__ = 'domains'
    id = Column(String(26), primary_key=True, default=generate_ulid)
    organization_id = Column(String(26), ForeignKey('organizations.id'))
```

---

## Laravel API Migration (mailyte-api)

### Step 1: Install ULID Package (5 min)

Laravel 12 has built-in ULID support via `Illuminate\Support\Str::ulid()`.

```bash
# No additional package needed — Laravel 12 includes ULID natively
```

### Step 2: Create Migration for All Tables (1 day)

```bash
php artisan make:migration convert_all_tables_to_ulid_primary_keys
```

**Migration file:**

```php
<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;
use Illuminate\Support\Facades\DB;

return new class extends Migration
{
    /**
     * Tables with auto-increment INT id → ULID CHAR(26)
     */
    private array $intIdTables = [
        'users',
        'domains',
        'mailboxes',
        'aliases',
        'subscriptions',
        'plans',
        'invoices',
        'api_keys',
        'invitations',
        'hosts',
        'data_operations',
        'discounts',
        'activity_log',
    ];

    /**
     * Foreign key mappings: [table => [column => referenced_table]]
     */
    private array $foreignKeys = [
        'domains' => [
            'organization_id' => 'organizations',
            'user_id' => 'users',
        ],
        'mailboxes' => [
            'domain_id' => 'domains',
            'user_id' => 'users',
        ],
        'aliases' => [
            'domain_id' => 'domains',
        ],
        'subscriptions' => [
            'user_id' => 'users',
            'plan_id' => 'plans',
            'organization_id' => 'organizations',
        ],
        'invoices' => [
            'user_id' => 'users',
            'subscription_id' => 'subscriptions',
        ],
        'invitations' => [
            'organization_id' => 'organizations',
            'invited_by' => 'users',
        ],
    ];

    public function up(): void
    {
        // Step 1: Drop all foreign key constraints
        foreach ($this->foreignKeys as $table => $columns) {
            Schema::table($table, function (Blueprint $t) use ($columns) {
                foreach (array_keys($columns) as $column) {
                    try {
                        $t->dropForeign([$column]);
                    } catch (\Exception $e) {
                        // FK may not exist
                    }
                }
            });
        }

        // Step 2: Convert primary keys from INT to CHAR(26)
        foreach ($this->intIdTables as $table) {
            if (!Schema::hasTable($table)) continue;

            // Add new ULID column
            Schema::table($table, function (Blueprint $t) {
                $t->char('ulid_id', 26)->nullable()->after('id');
            });

            // Generate ULIDs for existing rows
            DB::table($table)->orderBy('id')->each(function ($row) use ($table) {
                DB::table($table)
                    ->where('id', $row->id)
                    ->update(['ulid_id' => (string) \Illuminate\Support\Str::ulid()]);
            });

            // Drop old PK, rename, set as new PK
            Schema::table($table, function (Blueprint $t) {
                $t->dropPrimary();
            });
            Schema::table($table, function (Blueprint $t) {
                $t->dropColumn('id');
            });
            Schema::table($table, function (Blueprint $t) {
                $t->renameColumn('ulid_id', 'id');
            });
            Schema::table($table, function (Blueprint $t) {
                $t->char('id', 26)->primary()->change();
            });
        }

        // Step 3: Convert organizations table (already VARCHAR → CHAR(26))
        if (Schema::hasTable('organizations')) {
            Schema::table('organizations', function (Blueprint $t) {
                $t->char('id', 26)->change();
            });
        }

        // Step 4: Convert foreign key columns to CHAR(26)
        foreach ($this->foreignKeys as $table => $columns) {
            if (!Schema::hasTable($table)) continue;
            Schema::table($table, function (Blueprint $t) use ($columns) {
                foreach ($columns as $column => $refTable) {
                    $t->char($column, 26)->nullable()->change();
                }
            });
        }

        // Step 5: Re-add foreign key constraints
        foreach ($this->foreignKeys as $table => $columns) {
            if (!Schema::hasTable($table)) continue;
            Schema::table($table, function (Blueprint $t) use ($columns) {
                foreach ($columns as $column => $refTable) {
                    try {
                        $t->foreign($column)->references('id')->on($refTable)->nullOnDelete();
                    } catch (\Exception $e) {
                        // Skip if referenced table doesn't exist
                    }
                }
            });
        }
    }
};
```

### Step 3: Update All Eloquent Models (1 day)

**Before:**
```php
class Domain extends Model
{
    protected $fillable = ['name', 'organization_id'];
}
```

**After:**
```php
use Illuminate\Database\Eloquent\Concerns\HasUlids;

class Domain extends Model
{
    use HasUlids;

    protected $fillable = ['name', 'organization_id'];

    // Laravel's HasUlids trait automatically:
    // - Generates ULID on creation
    // - Sets $keyType = 'string'
    // - Sets $incrementing = false
}
```

**Models to update:**

| Model | File | Changes |
|-------|------|---------|
| User | `app/Models/User.php` | Add `use HasUlids;` |
| Organization | `app/Models/Organization.php` | Add `use HasUlids;` |
| Domain | `app/Models/Domain.php` | Add `use HasUlids;` |
| Mailbox | `app/Models/Mailbox.php` | Add `use HasUlids;` |
| Alias | `app/Models/Alias.php` | Add `use HasUlids;` |
| Subscription | `app/Models/Subscription.php` | Add `use HasUlids;` |
| Plan | `app/Models/Plan.php` | Add `use HasUlids;` |
| Invoice | `app/Models/Invoice.php` | Add `use HasUlids;` |
| ApiKey | `app/Models/ApiKey.php` | Add `use HasUlids;` |
| Invitation | `app/Models/Invitation.php` | Add `use HasUlids;` |
| Host | `app/Models/Host.php` | Add `use HasUlids;` |
| DataOperation | `app/Models/DataOperation.php` | Add `use HasUlids;` |
| Discount | `app/Models/Discount.php` | Add `use HasUlids;` |

### Step 4: Update Route Model Binding (30 min)

Laravel auto-resolves model IDs from URLs. With ULID, the route parameter regex needs updating:

```php
// routes/api.php — No change needed!
// Laravel's HasUlids trait handles route model binding automatically.
// Routes like /api/domains/{domain} will resolve ULID strings.
```

But if you have explicit `where` constraints:

```php
// Before
Route::get('/domains/{domain}', ...)->where('domain', '[0-9]+');

// After — remove the constraint (ULIDs are alphanumeric)
Route::get('/domains/{domain}', ...);
```

### Step 5: Update API Resources / Responses (30 min)

```php
// Before (INT)
class DomainResource extends JsonResource
{
    public function toArray($request)
    {
        return [
            'id' => $this->id,  // was: 42
            ...
        ];
    }
}

// After (ULID) — no code change needed!
// $this->id now returns "01H5YGKB3ZJR4DPNZN9SMTHF4R" instead of 42
```

### Step 6: Update Frontend (mailyte-web) (1 day)

The Next.js frontend stores IDs as strings in state and URLs. Changes needed:

```typescript
// Before
interface Domain {
    id: number;
    name: string;
}

// After
interface Domain {
    id: string;  // ULID: "01H5YGKB3ZJR4DPNZN9SMTHF4R"
    name: string;
}
```

**Files to update:**
- `types/` — all TypeScript interfaces that have `id: number`
- `lib/api/` — any parseInt() calls on IDs
- `app/dashboard/` — URL construction with IDs
- `stores/` — Zustand state that stores IDs

### Step 7: Sync IDs Between Laravel and Email Server (Critical)

When Laravel creates a domain via the email server API, it gets back a ULID. This ULID must be stored in Laravel's DB for future reference.

```php
// In EmailServerClient:
$result = $emailServer->createDomain('example.com', $org->id);
$domain->update([
    'email_server_domain_id' => $result['data']['id'],  // ULID from email server
]);
```

Both systems generate their OWN ULIDs independently — they don't share the same ID. Laravel has its own `domains.id` ULID, and the email server has its own `domains.id` ULID. The mapping is stored in `email_server_domain_id`.

---

## Postfix/Dovecot Impact

**None.** Postfix and Dovecot query by `domain` (string) and `email` (string), not by ID:

```sql
-- Postfix: "Is this domain ours?"
SELECT 1 FROM domains WHERE domain = 'example.com' AND active = 1;
-- ✅ No ID involved

-- Dovecot: "Authenticate this user"
SELECT email, password FROM email_accounts WHERE email = 'user@example.com';
-- ✅ No ID involved

-- Dovecot: "Where's the mailbox?"
SELECT ... FROM email_accounts ea JOIN domains d ON ea.domain_id = d.id WHERE ea.email = 'user@example.com';
-- ⚠️ JOIN uses domain_id — but it's just a string comparison now (CHAR(26) = CHAR(26))
```

The JOIN still works — it just compares ULID strings instead of integers. Performance is slightly slower than INT JOINs but negligible with indexes.

---

## Migration Sequence

```
1. Email Server DB Migration (009_ulid_primary_keys.sql)
   └→ Run: python manage.py migrate
   └→ All email server tables converted to ULID

2. Email Server Code Update
   └→ SQLAlchemy models use ULID defaults
   └→ API route type hints: int → str
   └→ Rebuild: docker compose build api

3. Laravel DB Migration
   └→ Run: php artisan migrate
   └→ All Laravel tables converted to ULID

4. Laravel Model Update
   └→ Add HasUlids trait to all models
   └→ Remove $incrementing = true (default with HasUlids)

5. Frontend Update
   └→ Change id: number to id: string in TypeScript types
   └→ Remove parseInt() calls on IDs

6. Integration Test
   └→ Run: ./start.sh test
   └→ Verify domain creation → ULID returned
   └→ Verify mailbox creation → IMAP login works
   └→ Verify alias creation → forwarding works
```

---

## Rollback Plan

If the migration fails:
1. The SQL migration drops old INT columns — **this is destructive**
2. **Before running:** Take a full database backup
3. **Rollback:** Restore from backup

```bash
# Before migration
./start.sh db-backup

# Run migration
python manage.py migrate

# If something breaks
./start.sh db-restore storage/backups/backup_YYYYMMDD_HHMMSS.sql
```

---

## Timeline

| Task | System | Effort |
|------|--------|--------|
| SQL migration file | Email Server | Done (agent) |
| ULID utility module | Email Server | Done (agent) |
| SQLAlchemy model updates | Email Server | Done (agent) |
| Route type hint updates | Email Server | Done (agent) |
| Laravel migration | mailyte-api | 1 day |
| Laravel model updates | mailyte-api | 1 day |
| Frontend type updates | mailyte-web | 1 day |
| Integration testing | All | 1 day |

**Total: 4 days across all three systems.**
