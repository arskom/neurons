# encoding: utf8
#
# This file is part of the Neurons project.
# Copyright (c), Arskom Ltd. (arskom.com.tr),
#                Burak Arslan <burak.arslan@arskom.com.tr>.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
#
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
#
# * Neither the name of the Arskom Ltd. nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#

import logging
logging.basicConfig(level=logging.DEBUG)

import re
import unittest

from neurons.form import HtmlFormTable
from neurons.form.test import strip_ns
from neurons.form.const import T_TEST

from spyne.util.test import show
from spyne import Application, NullServer, ServiceBase, rpc, ComplexModel, \
    Integer, Array, Unicode
from lxml import etree, html


def _test_type(cls, inst):
    from spyne.util import appreg; appreg._applications.clear()

    class SomeService(ServiceBase):
        @rpc(_returns=cls, _body_style='bare')
        def some_call(ctx):
            return inst

    prot = HtmlFormTable(cloth=T_TEST)
    app = Application([SomeService], 'some_ns', out_protocol=prot)

    null = NullServer(app, ostr=True)

    ret = ''.join(null.service.some_call())
    try:
        elt = html.fromstring(ret)
    except:
        print(ret)
        raise

    show(elt)
    elt = elt.xpath('//*[@spyne]')[0]
    elt = strip_ns(elt)  # get rid of namespaces to simplify xpaths in tests

    print(etree.tostring(elt, pretty_print=True))

    return elt


class TestForm(unittest.TestCase):
    def test_simple_array(self):
        v = range(5)
        elt = _test_type(Array(Integer), v)[0]

        assert elt.xpath('table/tbody/tr/td/div/input/@value') == \
                                                       ['0', '1', '2', '3', '4']
        assert elt.xpath('table/tbody/tr/td/button/text()') == ['-'] * 5 + ['+']
        for i, name in enumerate(elt.xpath('div/div/input/@name')):
            assert re.match(r'ints\[0*%d\]' % i, name)

    def test_complex_array(self):
        v = "domates"  # 🍅

        class SomeObject(ComplexModel):
            i = Integer
            s = Unicode

        v = [SomeObject(i=i, s=s) for i, s in enumerate(v)]
        elt = _test_type(Array(SomeObject), v)[0]

        assert elt.xpath(
            'table/tbody/tr/td/div/input[@class="integer"]/@value') == \
                                                           [str(o.i) for o in v]
        assert elt.xpath(
            'table/tbody/tr/td/div/input[@class="string"]/@value') == \
                                                           [str(o.s) for o in v]


        assert elt.xpath('table/tbody/tr/td/button/text()') == ['-'] * 7 + ['+']
        for i, name in enumerate(elt.xpath('div/div/input/@name')):
            assert re.match(r'ints\[0*%d\]' % i, name)


if __name__ == '__main__':
    unittest.main()
