#!/usr/bin/env python3

#   Copyright (c) MediaTek USA Inc., 2020-2024
#
#   This program is free software;  you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 2 of the License, or (at
#   your option) any later version.
#
#   This program is distributed in the hope that it will be useful, but
#   WITHOUT ANY WARRANTY;  without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#   General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program;  if not, see
#   <http://www.gnu.org/licenses/>.
#
#
# This script traverses XML coverage data in
# https://raw.githubusercontent.com/cobertura/web/master/htdocs/xml/coverage-04.dtd
# format (e.g., generated by Cobertura or the Coverage.py module) and translates it
# into LCOV .info format.
#
# Arguably, this should be done in Perl so we could use lcovutil module utilities.
# In the meantime:  suggested use model is to translate the XML using this
# utility, then read it back into lcov for additonal processing.
#
# @todo figure out how to characterize branch expressions in XML data

import os
import os.path
import sys
import re
import xml.etree.ElementTree as ET
import fnmatch
import subprocess
import copy
import base64
import hashlib
import pdb

def line_hash(line: str) -> str:
    """Produce a hash of a source line for use in the LCOV file."""
    hashed = hashlib.md5(line.encode("utf-8")).digest()
    return base64.b64encode(hashed).decode("ascii").rstrip("=")


class ProcessFile:
    """Expected/support scriptArgs:
    args.input     : name of XML file
    args.outf      : output FILE handle (written to)
    args.testName  : LCOV test name (optional) - see 'man lcov'
    args.excludePatterns :
                     comma-separated list of glob patterns
    args.verbose   : verbosity
    args.version   : version script callback
    args.checksum  : compute base64 checksum for each line - see 'man lcov'
    args.isPython  : input XML file came from Coverage.py - so apply certain
                     Python-specific derivations.
    args.deriveFunctions :
                     derive function coverpoints (primarily useful for Python -
                     see 'py2lcov --help' and the Coverage.py documentation
    args.tabWidth  : tab width to assume when deriving information from indentation -
                     used during Python function derivation.
    args.keepGoing : do not stop when error or inconsistency is detected

    """

    usageNote = """

Note that the XML coverage data format does not contain enough information
to deduce exactly which branch expressions have been taken or not taken.

It reports the total number of branch expressions associated with a particular
line, and the number of those which have been taken. There is no way to know
(except, possibly by inspection of surrounding code and/or some understanding
of your implementation) exactly which ones.

This is a problem in at least 2 ways:

  - It is not straightforward to use the result to improve your regression
    suite because you don't really know what was exercised/not exercised.

  - Coverage data merge is problematic.

      o For eample: you have two testcase XML files, each of which hit
        4 of 8 branches on some line.

      o  Does that mean you hit 4 of them (both tests exercised the same
         code), all 8 (tests exercised disjoint subsets), or some number
         between?

    This implementation assumes that the first M branches are the ones
    which are hit and the remaining N-M were not hit, in each testcase.
    Thus, the combined result in the above example would claim 4 of 8
    branches hit.
    This definition turns out to be a lower bound.
"""

    def __init__(self, scriptArgs):
        self._args = scriptArgs

        self._excludePatterns = scriptArgs.excludePatterns.split(',') if scriptArgs.excludePatterns else None
        self._versionScript = scriptArgs.version.split(',') if scriptArgs.version else None
        if self._versionScript and self._versionScript[0][-3:] == ".pm":
            # hard to handle Perl module in python - so we hack it
            self._vesionModule = self._versionScript
            self._versionScript = None

        self._outf = open(scriptArgs.output, "w")
        try:
            self._isPython = scriptArgs.isPython
        except:
            self._isPython = False

        self._outf.write("TN:%s\n" % scriptArgs.testName)

    def close(self):

        self._outf.close()

        if self._args.version and None == self._versionScript:
            cmd = "%(lcov)s -a %(info)s -o %(info)s --version-script '%(vers)s' %(checksum)s--rc compute_file_version=1" % {
                'lcov': os.path.join(os.path.split(sys.argv[0])[0], 'lcov'),
                'checksum': "--checksum " if self._args.checksum else '',
                'info': self._args.output,
                'vers' : self._args.version,
            }
            try:
                x = subprocess.run(cmd, shell=True, check=True, stdout=True, stderr=True)
            except subprocess.CalledProcessError as err:
                print("Error during lcov version append operation: %s" % (
                    str(err)))
                if not self._args.keepGoing:
                    sys.exit(1);


    def process_xml_file(self, xml_file):

        tree = ET.parse(xml_file)
        root = tree.getroot()
        source_paths = []

        try:
            if(root[0].tag == 'sources'):
                for source in root[0]:
                    # keep track of number of times we use each source_path to find
                    #  some file.  Unused source paths are likely a problem.
                    source_paths.append([source.text, 0])
                    if self._args.verbose:
                        print("source: " + source.text)
            else:
                print("Error: parse xml fail: no 'sources' in %s" %(xml_file))
                sys.exit(1)
            if(root[1].tag == 'packages'):
                if (self._args.verbose):
                    print("packages: " + str(root[1].attrib))
            else:
                print("Error: parse xml fail: no 'packages' in %s" %(xml_file))
                sys.exit(1)
        except Exception as err:
            print("Error: parse xml fail in %s: %s" % (xml_file, str(err)))
            if not self._args.keepGoing:
                sys.exit(1)
            return

        for package in root[1]:
            # name="." means current directory
            # name=".folder1.folder2" means external module or directory
            # name="abc" means internal module or directory
            isExternal = (package.attrib['name'].startswith('.') and package.attrib['name'] != '.')
            #pdb.set_trace()
            for classes in package:
                for fileNode in classes:
                    if self._args.excludePatterns and any([fnmatch.fnmatchcase(fileNode.attrib['filename'], ef) for ef in self._excludePatterns]):
                        if self._args.verbose:
                            print("%s is excluded" % fileNode.attrib['filename'])
                        continue
                    name = fileNode.attrib['filename']
                    if not isExternal:
                        for s in source_paths:
                            path = os.path.join(s[0], name)
                            if os.path.exists(path):
                                name = path
                                s[1] += 1 # this source path used for something
                                break
                        else:
                            print("did not find %s in search path" % (path))

                    self._outf.write("SF:%s\n" % name)
                    if self._versionScript:
                        cmd = copy.deepcopy(self._versionScript)
                        cmd.append(name)
                        try:
                            version = subprocess.check_output(cmd)
                            self._outf.write("VER:%s\n" % version.strip().decode('UTF-8'))
                        except Exception as err:
                            print("Error: no version for %s: %s" %(
                                name, str(err)))
                            if not self._args.keepGoing:
                                sys.exit(-1)

                    self.process_file(fileNode, name)
                    self._outf.write("end_of_record\n")

        for s in source_paths:
            if s[1] == 0:
                print("Warning: XM file '%s': source_path '%s' is unused" %(xml_file, s[0]))


    def process_file(self, fileNode, filename):

        sourceCode = None
        if (self._args.checksum or
            (self._isPython and self._args.deriveFunctions)):
            try:
                with open(filename, 'r') as f:
                    sourceCode = f.read().split('\n')
            except:
                feature = ' compute line checksum' if self._args.checksum else ''
                if self._isPython and self._args.deriveFunctions:
                   if feature != '':
                      feature += ' or'
                   feature += ' derive function data'

                print("cannot open %s - unable to %s" % (filename, feature));
                if not self._args.keepGoing:
                    sys.exit(1)

        def count(indent):
            count = 0
            for c in indent:
                if c == ' ':
                    count += 1
                else:
                    assert(c == '\t') # shouldn't be anything but space or tab
                    count += self._args.tabWidth
            return count

        def buildFunction(functions, objStack, currentObj, lastLine):
            if currentObj and prevLine:
                currentObj['end'] = lastLine # last line
                prefix = ''
                sep = ''
                for e in objStack:
                    prefix += sep + e['name']
                    sep = "::" if e['type'] == 'class' else '.'
                if currentObj['type'] == 'def':
                    fullname = prefix + sep + currentObj['name']
                    # function might be unreachable dead code
                    try:
                        hit = currentObj['hit']
                    except:
                        hit = 0
                    functions.append({'name'  : fullname,
                                      'start' : currentObj['start'],
                                      'end'   : currentObj['end'],
                                      'hit'   : hit})

        # just collect the function/class name - ignore the params
        parseLine = re.compile('(\s*)((def|class)\s*([^\( \t]+))?')
        #parseLine = re.compile('(\s*)((def|class)\s*([^:]+)(:|$))?')

        # no information about actual branch expressions/branch
        #  coverage - only the percentage and number hit/not hit
        parseCondition = re.compile(r'\d+\% \((\d+)/(\d+)\)')

        functions = [] # list of [functionName startLine endLine hitcout]
        for node in fileNode:

            if node.tag == 'methods':
                # build function decls...
                for method in node:
                    func = method.attrib['name']

                    # does this method contain any lines?
                    for lines in method:
                        assert(lines.tag == 'lines')
                        first = None
                        last = None
                        hit = 0
                        if lines.tag == 'lines':
                            # might want to hang onto the method lines - and
                            #   check that the 'lines' tag we find in the parent
                            #   node contains all of the method lines we found
                            functionLines = {}
                            branches = {}
                            for l in lines:
                                lineNum = int(l.attrib['number'])
                                lineHit = int(l.attrib['hits'])
                                functionLines[lineNum] = lineHit
                                if first == None:
                                    first = lineNum
                                    last = lineNum
                                    hit = lineHit
                                else:
                                    assert(lineNum > last)
                                    last = lineNum;
                                if 'branch' in l.attrib and 'true' == l.attrib['branch']:
                                    assert('condition-coverage' in l.attrib)
                                    m = parseCondition.search(l.attrib['condition-coverage'])
                                    assert(m)
                                    # [taken total]
                                    branches[lineNum] = [m.group(1), m.group(2)]

                        if first != None:
                            functions.append({'name'  : func,
                                              'start' : first,
                                              'end'   : last,
                                              'hit'   : hit,
                                              'lines' : functionLines,
                                              'branches' : branches})
                        elif self._args.verbose:
                            # there seem to be a fair few functions
                            #  which contain no data
                            print("elided empty function %s" %(func))

                continue

            if node.tag != 'lines':
                print("not handling tag %s" %(node.tag))
                continue

            # Keep track of current function/class scope - which we use to find
            #   the first and last executable lines in each function,
            # Want to keep track of the function end line - so we can use lcov
            # function exclusions.
            #   currentObj:
            #    type:   'class' or 'def'
            #    name:   as appears in regexp
            #    indent: indent count of 'def' or 'class' statement
            #    start:  line of item (where 'def' or 'class' is found
            #    end:    last line of function
            #    hit:    whether first line of function is hit or not
            currentObj = None # {type name startIndent lineNo first end start}
            objStack = []
            prevLine = None
            totals = { 'line' : [0, 0, 'LF', 'LH'],
                       'branch' : [0, 0, 'BRF', 'BRH'],
                       'function' : [0, 0, 'FNF', 'FNH'],
            }
            # need to save the statement data and print later because Coverage.py
            # has an odd interpretation of the execution status of the function
            # decl line.
            #   - C/C++ mark it executed if the line is entered - so it
            #     is an analog of function coverage.
            #   - Coverage.py appears to mark it executed when the containing
            #     scope is executed (i.e., when a lazy interpret might compile
            #     the function).
            # However, we want to mark the decl executed only if the function
            # is executed - and we decide that the function is executed if first
            # line in the function is hit.
            #   - as a result, after seeing all the functions, we want to go back
            #     and mark the function decl line as 'not hit' if we decided that
            #     the function itself is not executed.
            lineData = {}
            for line in node:
                lineNo = int(line.attrib['number'])
                hit = int(line.attrib["hits"])
                lineData[lineNo] = hit;

                totals['line'][0] += 1
                if hit:
                    totals['line'][1] += 1
                if sourceCode and self._isPython:
                    # try to derive function names and begin/end lines in Python code
                    if lineNo <= len(sourceCode):
                        m = parseLine.search(sourceCode[lineNo-1])
                        if m:
                            indent = count(m.group(1))
                            #print(sourceCode[lineNo-1])
                            while currentObj and indent <= currentObj['indent']:
                                # lower indent - so this is a new object
                                #print("build " + currentObj['name'])
                                buildFunction(functions, objStack,
                                              currentObj, prevLine)

                                try:
                                    currentObj = objStack.pop()
                                except IndexError as err:
                                    currentObj = None
                                    break

                            if m.group(2):
                                if currentObj:
                                    objStack.append(currentObj)
                                objtype = m.group(3)
                                name = m.group(4).rstrip()
                                if (-1 != name.find('(') and
                                    ')' != name[-1]):
                                    name += '...)'
                                currentObj = { 'type':   objtype,
                                               'name':   name,
                                               'indent': indent,
                                               'start':  lineNo,
                                }
                            else:
                                # just a line - may be the first executable
                                #   line in some function:
                                if currentObj and not 'hit' in currentObj:
                                    currentObj['hit'] = hit
                                    # mark that function decl line is not
                                    #  hit if the function is not hit
                                    if 0 == hit:
                                        assert(currentObj['start'] in lineData)
                                        lineData[currentObj['start']] = 0

                        prevLine = lineNo
                    else:
                        print('"%s":%d: Error: out of range: file contains %d lines' % (
                            filename, lineNo, len(sourceCode)))
                        if not self._args.keepGoing:
                            sys.exit(1)

                if "branch" in line.attrib and line.attrib["branch"] == 'true':
                    # attrib is always true from xmlreport.py - but may not
                    #   be true cobertura report
                    assert('condition-coverage' in line.attrib)
                    m = parseCondition.search(line.attrib['condition-coverage'])
                    assert(m)
                    taken = int(m.group(1))
                    total = int(m.group(2))
                    # no information of which clause is taken or not
                    # set taken conditions start from 0 and followed by
                    #  non-taken conditions
                    # taken conditions
                    for cond in range(0,taken):
                        self._outf.write("BRDA:%d,0,%d,1\n" % (lineNo, cond))
                        totals['branch'][0] += 1
                        totals['branch'][1] += 1
                    # non-taken conditions
                    for cond in range(taken, total):
                        totals['branch'][0] += 1
                        self._outf.write("BRDA:%d,0,%d,0\n" % (lineNo, cond))

            # and build all the pending functions
            #  these were still open when we hit the end of file - e.g., because
            #  they are last elements in some package file and there are no
            #  no executable lines after the function decl.
            # There may be more than one function in the stack, if the last
            # object is nested.
            while currentObj:
                buildFunction(functions, objStack, currentObj, prevLine)

                try:
                    currentObj = objStack.pop()
                except IndexError as err:
                    currentObj = None
                    break

            # print the LCOV function data
            idx = 0
            for f in functions:
                totals['function'][0] += 1
                f['idx'] = idx
                idx += 1
                if f['hit']:
                    totals['function'][1] += 1
                self._outf.write("FNL:%(idx)d,%(start)d,%(end)d\nFNA:%(idx)d,%(hit)d,%(name)s\n" % f)
            # print the LCOV line data.
            for lineNo in sorted(lineData.keys()):
                checksum = ''
                if self._args.checksum:
                    try:
                        checksum = ',' + line_hash(sourceCode[lineNo-1])
                    except IndexError as err:
                        print('"%s":%d: unable to compute checksum for missing line' % (filename, lineNo))
                        if not self._args.keepGoing:
                            raise(err)

                self._outf.write("DA:%d,%d%s\n" % (lineNo, lineData[lineNo], checksum));

            # print the LCOV totals - not used by lcov, but maybe somebody does
            for key in totals:
                d = totals[key]
                if d[0] == 0:
                    continue
                self._outf.write("%s:%d\n" % (d[2], d[0]))
                self._outf.write("%s:%d\n" % (d[3], d[1]))
