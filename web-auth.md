# Supavision — Web Auth & Users Foundation (Pre-Launch Requirement)

## Context

The backend, scanner, metrics system, and security hardening are strong. However, the **web product layer is missing core authentication and user management**, which is a **launch-blocking gap**.

A system cannot be considered production-ready if:

* there is no login system
* there is no users table
* there is no session model
* dashboard routes are not protected
* roles and access control are undefined

This phase closes the gap between a strong backend and a secure, real-world web application.

---

# Objective

Introduce a **complete authentication and user management foundation** so the dashboard can be safely exposed.

---

# 1. Core Requirements

## 1.1 Users Table

Create a `users` table:

```sql
users:
- id (uuid or int)
- email (unique, indexed)
- password_hash
- name
- role (admin, viewer)
- is_active (boolean)
- created_at
- last_login_at
```

---

## 1.2 Sessions

Create a `sessions` table:

```sql
sessions:
- id (session token)
- user_id
- created_at
- expires_at
- revoked_at
- ip_address
- user_agent
```

---

## 1.3 Authentication Flow

### Routes

* `/login`
* `/logout`

### Behavior

* email + password authentication
* password stored securely (hashed)
* session cookie issued on login
* logout invalidates session
* redirect user to intended page after login

---

## 1.4 Protected Routes

All dashboard routes must require authentication:

* `/dashboard`
* `/resources`
* `/findings`
* `/activity`
* `/settings`
* all API endpoints used by the UI

Unauthorized users must be redirected to `/login`.

---

## 1.5 Roles

Minimum roles:

* `admin`: full access
* `viewer`: read-only

Future roles optional, but not required for launch.

---

## 1.6 Admin Bootstrap

A safe method to create the first admin user is required.

Recommended:

```bash
supavision create-admin
```

Alternative:

* one-time setup screen with token (less preferred)

---

## 1.7 Security Requirements

### Must-have

* CSRF protection on authenticated POST actions
* secure cookies:

  * httpOnly
  * secure (in production)
  * sameSite
* login rate limiting
* generic login errors (no user enumeration)
* session expiration
* password policy
* account activation/deactivation support

---

## 1.8 User Management UI

Add `/settings/users`:

Features:

* list users
* create user
* deactivate user
* change role
* reset password (admin-triggered)

---

## 1.9 Audit Logging

Log the following events:

* login success
* login failure
* logout
* user created
* user deactivated
* role changes
* password reset

---

# 2. Implementation Phases

## Phase S1 — Backend Auth Foundation

* users table
* sessions table
* password hashing
* auth middleware/dependency
* login/logout endpoints
* CLI admin bootstrap

---

## Phase S2 — Web Integration

* `/login` page UI
* route protection middleware
* redirect handling
* session-aware layout

---

## Phase S3 — User Management

* `/settings/users`
* CRUD operations for users
* role assignment
* audit logging

---

## Phase S4 — Hardening

* rate limiting
* session expiration policies
* lockout/backoff strategy
* security tests

---

# 3. Release Gate (Must Pass Before Go-Live)

Do NOT go live until:

* admin user can be created safely
* login/logout works correctly
* all dashboard routes are protected
* roles are enforced
* session cookies are hardened
* login is rate limited
* basic user management exists
* audit logging is active

---

# 4. Key Principle

> Strong backend security is not sufficient.
> A production system must also secure access to its interface.