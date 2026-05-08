"""frontend_server URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/2.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf.urls import include, url
from django.urls import path
from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static

from translator import views as translator_views
from translator import vending_api

urlpatterns = [
    url(r'^$', translator_views.landing, name='landing'),
    url(r'^simulator_home$', translator_views.home, name='home'),
    url(r'^demo/(?P<sim_code>[\w-]+)/(?P<step>[\w-]+)/(?P<play_speed>[\w-]+)/$', translator_views.demo, name='demo'),
    url(r'^replay/(?P<sim_code>[\w-]+)/(?P<step>[\w-]+)/$', translator_views.replay, name='replay'),
    url(r'^replay_persona_state/(?P<sim_code>[\w-]+)/(?P<step>[\w-]+)/(?P<persona_name>[\w-]+)/$', translator_views.replay_persona_state, name='replay_persona_state'),
    url(r'^process_environment/$', translator_views.process_environment, name='process_environment'),
    url(r'^update_environment/$', translator_views.update_environment, name='update_environment'),
    url(r'^path_tester/$', translator_views.path_tester, name='path_tester'),
    url(r'^path_tester_update/$', translator_views.path_tester_update, name='path_tester_update'),
    url(r'^api/vending/state/(?P<sim_code>[\w\-.]+)/$',        vending_api.vending_state_api,        name='vending_state_api'),
    url(r'^api/vending/journal/(?P<sim_code>[\w\-.]+)/$',      vending_api.vending_journal_api,      name='vending_journal_api'),
    url(r'^api/vending/personas/(?P<sim_code>[\w\-.]+)/$',     vending_api.vending_personas_api,     name='vending_personas_api'),
    url(r'^api/vending/sla/(?P<sim_code>[\w\-.]+)/$',          vending_api.vending_sla_api,          name='vending_sla_api'),
    url(r'^api/vending/governance/(?P<sim_code>[\w\-.]+)/$',   vending_api.vending_governance_api,   name='vending_governance_api'),
    url(r'^api/vending/supplier/(?P<sim_code>[\w\-.]+)/$',     vending_api.vending_supplier_api,     name='vending_supplier_api'),
    url(r'^api/vending/daily_report/(?P<sim_code>[\w\-.]+)/$', vending_api.vending_daily_report_api, name='vending_daily_report_api'),
    url(r'^api/vending/reconcile/(?P<sim_code>[\w\-.]+)/$',    vending_api.vending_reconcile_api,    name='vending_reconcile_api'),
    path('admin/', admin.site.urls),
]
