#
# Project Ginger Base
#
# Copyright IBM Corp, 2015-2017
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA

import cherrypy
import json
import os
import tempfile

from wok.plugins.gingerbase import config, mockmodel
from wok.plugins.gingerbase.i18n import messages
from wok.plugins.gingerbase.control import sub_nodes
from wok.plugins.gingerbase.model import model as gingerBaseModel
from wok.root import WokRoot


class Gingerbase(WokRoot):
    def __init__(self, wok_options):
        make_dirs = [
            os.path.dirname(os.path.abspath(config.get_object_store())),
            os.path.abspath(config.get_debugreports_path())
        ]
        for directory in make_dirs:
            if not os.path.isdir(directory):
                os.makedirs(directory)

        if wok_options.test and (wok_options.test is True or
                                 wok_options.test.lower() == 'true'):
            self.objectstore_loc = tempfile.mktemp()
            self.model = mockmodel.MockModel(self.objectstore_loc)

            def remove_objectstore():
                if os.path.exists(self.objectstore_loc):
                    os.unlink(self.objectstore_loc)
            cherrypy.engine.subscribe('exit', remove_objectstore)
        else:
            self.model = gingerBaseModel.Model()

        dev_env = wok_options.environment != 'production'
        super(Gingerbase, self).__init__(self.model, dev_env)

        for ident, node in sub_nodes.items():
            setattr(self, ident, node(self.model))

        self.api_schema = json.load(open(os.path.join(os.path.dirname(
                                    os.path.abspath(__file__)), 'API.json')))
        self.paths = config.gingerBasePaths
        self.domain = 'gingerbase'
        self.messages = messages

    def get_custom_conf(self):
        return config.GingerBaseConfig()
