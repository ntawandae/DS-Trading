from functools import wraps
from flask import abort
from flask_login import current_user


def roles_required(*roles):
    """Restrict a route to users whose .role is in `roles`. Use after @login_required."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated or current_user.role not in roles:
                abort(403)
            return f(*args, **kwargs)
        return wrapped
    return decorator


def admin_required(f):
    return roles_required("admin")(f)


def finance_required(f):
    """Admin or Finance/HR — payroll, financial reports. Not general workers."""
    return roles_required("admin", "finance_hr")(f)


def staff_required(f):
    """Admin, Finance/HR, or worker — i.e. anyone allowed into the internal admin panel at all."""
    return roles_required("admin", "finance_hr", "worker")(f)
