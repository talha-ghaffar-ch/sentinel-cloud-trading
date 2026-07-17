# 06 — Security

## Threat Model

| Threat | Mitigation |
|---|---|
| Credential theft | bcrypt hashing (cost 12), never stored plaintext |
| Brute force login | flask-limiter: 10 req/min on login, 5/hr on register |
| SQL injection | psycopg2 parameterized queries throughout — no string formatting |
| Session hijacking | HTTP-only cookies, SameSite=Lax, signed with SECRET_KEY |
| CSRF | SameSite cookie policy + session-based auth |
| Credential exposure in code | All secrets in environment variables, `.env` in `.gitignore` |
| Unauthorized AWS access | IAM instance profile on EC2 — no access keys in code |
| Privilege escalation | Role-based decorators: `@login_required`, `@verified_required`, `@admin_required` |
| Admin data leakage | MT5 passwords hidden by default in admin panel — explicit toggle required |
| Audit accountability | Every admin action logged to `audit_log` with actor, timestamp, IP |

---

## Password Security

Passwords are hashed using **bcrypt** with a cost factor of 12:

```python
import bcrypt
hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()
```

Verification:
```python
bcrypt.checkpw(input.encode(), stored_hash.encode())
```

At cost factor 12, each hash takes approximately 300–500ms to compute, making brute force computationally infeasible even with leaked hashes.

---

## Authentication Decorators

Three levels enforced via Python decorators in `auth.py`:

```python
@login_required      # user_id must exist in session
@verified_required   # account_status must be 'verified'
@admin_required      # role must be 'admin'
```

These wrap every protected route in `app.py`. An unauthenticated request to any protected route is redirected to `/login` immediately.

---

## AWS Security

**IAM Role (no hardcoded keys):**
The EC2 instance running the trading engine has an IAM instance profile attached with `AmazonDynamoDBFullAccess`. boto3 automatically retrieves temporary credentials from the EC2 instance metadata service — no `AWS_ACCESS_KEY_ID` or `AWS_SECRET_ACCESS_KEY` is ever stored anywhere.

**Security Groups:**
- RDS accepts TCP 5432 only from the EB security group and Windows EC2 private IP
- Windows EC2 accepts TCP 80 from anywhere (public web), TCP 3389 only from admin IP
- DynamoDB is accessed via AWS endpoints — no inbound security group rules needed

**Environment Variables:**
All sensitive values (RDS password, Flask secret key, DynamoDB table names) are stored as Elastic Beanstalk environment properties, injected at runtime. They never appear in source code or git history.

---

## Password Strength Validation

```python
def validate_password(pw):
    if len(pw) < 8:
        return False, "Password must be at least 8 characters."
    if not re.search(r"[A-Z]", pw):
        return False, "Password must contain at least one uppercase letter."
    if not re.search(r"[0-9]", pw):
        return False, "Password must contain at least one number."
    return True, ""
```

---

## Audit Log

Every admin action writes an immutable record:

```python
INSERT INTO audit_log (actor_id, action, target_type, target_id, details, ip_address)
VALUES (%s, %s, %s, %s, %s::jsonb, %s)
```

Actions logged: `APPLICATION_APPROVED`, `APPLICATION_REJECTED`, `USER_SUSPENDED`

The audit_log table has no UPDATE or DELETE privileges in normal application code.
