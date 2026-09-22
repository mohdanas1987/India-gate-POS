"""Import every domain module so Base.metadata is complete for Alembic
autogenerate and for `Base.metadata.create_all()` in tests."""
from app.domain import tenancy  # noqa: F401
from app.domain import authz  # noqa: F401
from app.domain import catalog  # noqa: F401
from app.domain import inventory  # noqa: F401
from app.domain import orders  # noqa: F401
from app.domain import cash  # noqa: F401
from app.domain import website  # noqa: F401
from app.domain import sync  # noqa: F401
from app.domain import customer  # noqa: F401
from app.domain import integrations  # noqa: F401
from app.domain import held_cart  # noqa: F401
