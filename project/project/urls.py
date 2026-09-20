from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from core.views import (
    SPA_ROUTES,
    frontend_index,
    health_check,
    submit_contact_form,
    submit_portfolio_contact,
)
from core.views import (
    handler400 as h400,
)
from core.views import (
    handler403 as h403,
)
from core.views import (
    handler404 as h404,
)
from core.views import (
    handler405 as h405,
)
from core.views import (
    handler500 as h500,
)

handler400 = h400
handler403 = h403
handler404 = h404
handler405 = h405
handler500 = h500

# Everything the academy's staff and families use is mounted under
# settings.APP_URL_PREFIX ("app/"), leaving "/" free for the public React site
# that becomes the home page. See the APP MOUNT POINT block in settings.py for
# why the prefix is a constant and what deliberately stays at the root.
_APP = f"{settings.APP_URL_PREFIX}/"

urlpatterns = [
    # ORIGIN ROOT — deliberately NOT under the app prefix.
    # The deploy pipeline, the QA sign-off gate and the uptime checks all poll
    # /health/ at the root; moving it would silently break the version compare
    # in both deploy workflows.
    path("health/", health_check, name="health_check"),
    # "/" is the PUBLIC React site (frontend/, built to frontend/dist). It must
    # stay reachable while logged out — SimpleAuthMiddleware passes every
    # non-app path through for exactly that reason.
    path("", frontend_index, name="public_home"),
    # The site's own client-side routes. Each is served the SAME shell and
    # React picks the page — the rewrite Netlify did with `/* -> /index.html`.
    # ENUMERATED rather than a catch-all so an unknown path is still a real
    # 404; see SPA_ROUTES in core/views/frontend.py.
    # No trailing slash: these are registered exactly as React Router and the
    # site's own nav links spell them ("/quienes-somos"). Adding one would make
    # every direct load and refresh cost an APPEND_SLASH redirect first.
    *[path(route, frontend_index, name=f"public_{route.replace('-', '_')}") for route in SPA_ROUTES],
    # The public contact form ("Contacta con nosotras"). Public and
    # rate-limited; it emails the academy.
    path("api/contact/", submit_contact_form, name="submit_contact_form"),
    # The owner's PERSONAL PORTFOLIO posts its contact form here — a second
    # tenant of this deployment, not an academy feature. Server-to-server only
    # (a Netlify Function holds the bearer token), which is why it needs no CORS
    # headers: no browser ever calls it directly. At the ORIGIN ROOT rather than
    # under the app prefix, so SimpleAuthMiddleware passes it through instead of
    # answering a token-authenticated POST with a redirect to the staff login.
    path("api/portfolio/contact/", submit_portfolio_contact, name="submit_portfolio_contact"),
    # THE APP
    path(f"{_APP}admin/", admin.site.urls),
    path(_APP, include("students.urls")),
    path(_APP, include("billing.urls")),
    path(_APP, include("comms.urls")),
    path(_APP, include("core.urls")),
]
