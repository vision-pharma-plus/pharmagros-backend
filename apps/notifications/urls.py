from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api import AnnouncementViewSet, NotificationViewSet

router = DefaultRouter()
router.register("notifications", NotificationViewSet, basename="notification")
router.register("announcements", AnnouncementViewSet, basename="announcement")

urlpatterns = [path("", include(router.urls))]
