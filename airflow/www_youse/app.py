# -*- coding: utf-8 -*-
#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#
import six

from flask import Flask
from flask_admin import Admin, base
from flask_appbuilder import AppBuilder, SQLA
from flask_caching import Cache
from flask_wtf.csrf import CSRFProtect
from six.moves.urllib.parse import urlparse
from werkzeug.wsgi import DispatcherMiddleware
from werkzeug.contrib.fixers import ProxyFix

import airflow
from airflow import configuration as conf
from airflow import models, LoggingMixin

from airflow.www_youse.blueprints import routes
from airflow.logging_config import configure_logging
from airflow import jobs
from airflow import settings
from airflow import configuration
from airflow.utils.net import get_hostname

app = None
appbuilder = None
csrf = CSRFProtect()

def create_app(config=None, session=None, testing=False):
    global app, appbuilder

    app = Flask(__name__)
    if configuration.conf.getboolean('webserver', 'ENABLE_PROXY_FIX'):
        app.wsgi_app = ProxyFix(app.wsgi_app)
    app.secret_key = configuration.conf.get('webserver', 'SECRET_KEY')

    app.config['LOGIN_DISABLED'] = not configuration.conf.getboolean('webserver', 'AUTHENTICATE')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['TESTING'] = testing

    csrf.init_app(app)
    db = SQLA(app)

    airflow.load_login()
    airflow.login.login_manager.init_app(app)

    from airflow import api
    api.load_auth()
    api.api_auth.init_app(app)

    # flake8: noqa: F841
    cache = Cache(app=app, config={'CACHE_TYPE': 'filesystem', 'CACHE_DIR': '/tmp'})

    app.register_blueprint(routes)

    configure_logging()

    with app.app_context():
        appbuilder = AppBuilder(
            app,
            db.session if not session else session,
            base_template='appbuilder/baselayout.html')

        init_views(appbuilder)
        integrate_plugins(appbuilder)

        import airflow.www.api.experimental.endpoints as e
        # required for testing purposes otherwise the module retains
        # a link to the default_auth
        if app.config['TESTING']:
            six.moves.reload_module(e)

        app.register_blueprint(e.api_experimental, url_prefix='/api/experimental')

        @app.context_processor
        def jinja_globals():
            return {
                'hostname': get_hostname(),
                'navbar_color': configuration.get('webserver', 'NAVBAR_COLOR'),
            }

        @app.teardown_appcontext
        def shutdown_session(exception=None):
            settings.Session.remove()

        return app, appbuilder


def integrate_plugins(appbuilder):
    """Integrate plugins to the context"""
    from airflow.plugins_manager import (flask_appbuilder_views, flask_appbuilder_menu_links)

    log = LoggingMixin().log

    for v in flask_appbuilder_views:
        log.debug("Adding view %s", v["name"])
        appbuilder.add_view(v["view"],
                            v["name"],
                            category=v["category"])

    for ml in sorted(flask_appbuilder_menu_links, key=lambda x: x["name"]):
        log.debug("Adding menu link %s", ml["name"])
        appbuilder.add_link(ml["name"],
                            href=ml["href"],
                            category=ml["category"],
                            category_icon=ml["category_icon"])



def init_views(appbuilder):
    from airflow.www_youse import views
    appbuilder.add_view_no_menu(views.Airflow())
    appbuilder.add_view_no_menu(views.DagModelView())
    appbuilder.add_view_no_menu(views.ConfigurationView())
    appbuilder.add_view_no_menu(views.VersionView())
    appbuilder.add_view(views.DagRunModelView,
        "DAG Runs",
        category="Browse",
        category_icon="fa-globe")


def root_app(env, resp):
    resp(b'404 Not Found', [(b'Content-Type', b'text/plain')])
    return [b'Apache Airflow is not at this location']


def cached_app(config=None, testing=False):
    global app
    if not app:
        base_url = urlparse(configuration.conf.get('webserver', 'base_url'))[2]
        if not base_url or base_url == '/':
            base_url = ""

        app = create_app(config, testing)
        app = DispatcherMiddleware(root_app, {base_url: app})
    return app
