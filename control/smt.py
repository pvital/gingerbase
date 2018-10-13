#
# Project Ginger Base
#
# Copyright IBM Corp, 2016-2017
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

from wok.control.base import Resource
from wok.control.utils import UrlSubNode

SMT_ACTIVITY = {
    'POST': {
        'enable': "GINSMT0001L",
        'disable': "GINSMT0002L",
    },
}


@UrlSubNode('smt', True)
class Smt(Resource):
    def __init__(self, model, id=None):
        super(Smt, self).__init__(model, id)
        self.admin_methods = ['GET']
        self.uri_fmt = '/host/smt/%s'
        self.enable = self.generate_action_handler('enable', ['smt_val'])
        self.disable = self.generate_action_handler('disable')
        self.log_map = SMT_ACTIVITY

    @property
    def data(self):
        return self.info
