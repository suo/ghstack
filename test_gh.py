from __future__ import print_function
import textwrap
import doctest
import expecttest
import unittest
import subprocess
import warnings
import os
import shutil
import tempfile
import re

import gh


GH_KEEP_TMP = os.getenv('GH_KEEP_TMP')


def strip_trailing_whitespace(text):
    return re.sub(r' +$', '', text, flags=re.MULTILINE)


def indent(text, prefix):
    return ''.join(prefix+line if line.strip() else line for line in text.splitlines(True))


def create_pr(github):
    github.graphql("""
      mutation {
        createPullRequest(input: {
            baseRefName: "master",
            headRefName: "blah",
            title: "New PR",
            body: "What a nice PR this is",
            ownerId: 1000,
          }) {
          pullRequest {
            number
          }
        }
      }
    """)


class TestGh(expecttest.TestCase):
    # Starting up node takes 0.7s.  Don't do it every time.
    @classmethod
    def setUpClass(cls):
        port = 49152
        # Find an open port to run our tests on
        while True:
            cls.proc = subprocess.Popen(['node', 'github-fake/src/index.js', str(port)], stdout=subprocess.PIPE)
            r = cls.proc.stdout.readline()
            if not r.strip():
                cls.proc.terminate()
                cls.proc.wait()
                port +=1
                print("Retrying with port {}".format(port))
                continue
            break
        cls.github = gh.Endpoint("http://localhost:{}".format(port))

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait()

    def setUp(self):
        self.github.graphql("""
          mutation {
            resetGitHub(input: {}) {
              clientMutationId
            }
          }
        """)
        tmp_dir = tempfile.mkdtemp()

        # Set up a "parent" repository with an empty initial commit that we'll operate on
        upstream_dir = tempfile.mkdtemp()
        if GH_KEEP_TMP:
            self.addCleanup(lambda: print("upstream_dir preserved at: {}".format(upstream_dir)))
        else:
            self.addCleanup(lambda: shutil.rmtree(upstream_dir))
        self.upstream_sh = gh.Shell(cwd=upstream_dir, testing=True)
        self.upstream_sh.git("init", "--bare")
        tree = self.upstream_sh.git("write-tree")
        commit = self.upstream_sh.git("commit-tree", tree, input="Initial commit")
        self.upstream_sh.git("branch", "-f", "master", commit)

        local_dir = tempfile.mkdtemp()
        if GH_KEEP_TMP:
            self.addCleanup(lambda: print("local_dir preserved at: {}".format(local_dir)))
        else:
            self.addCleanup(lambda: shutil.rmtree(local_dir))
        self.sh = gh.Shell(cwd=local_dir, testing=True)
        self.sh.git("clone", upstream_dir, ".")

        self.rev_map = {}
        self.substituteRev("HEAD", "rINI0")

    def lookupRev(self, substitute):
        return self.rev_map[substitute]

    def substituteRev(self, rev, substitute):
        # short doesn't really have to be here if we do substituteRev
        h = self.sh.git("rev-parse", "--short", rev)
        self.rev_map[substitute] = h
        print("substituteRev: {} = {}".format(substitute, h))
        self.substituteExpected(h, substitute)

    def gh(self):
        gh.main(github=self.github, sh=self.sh)

    def dump_github(self):
        r = self.github.graphql("""
          query {
            repository(name: "pytorch", owner: "pytorch") {
              pullRequests {
                nodes {
                  number
                  baseRefName
                  headRefName
                  title
                  body
                }
              }
            }
          }
        """)
        prs = []
        for pr in r['data']['repository']['pullRequests']['nodes']:
            pr['body'] = indent(pr['body'], '    ')
            pr['commits'] = self.upstream_sh.git("log", "--reverse", "--pretty=format:%h %s", pr["baseRefName"] + ".." + pr["headRefName"])
            pr['commits'] = indent(pr['commits'], '     * ')
            prs.append("#{number} {title} ({headRefName} -> {baseRefName})\n\n"
                       "{body}\n\n{commits}\n\n".format(**pr))
            # TODO: Use of git --graph here is a bit of a loaded
            # footgun, because git doesn't really give any guarantees
            # about what the graph should look like.  So there isn't
            # really any assurance that this will output the same thing
            # on multiple test runs.  We'll have to reimplement this
            # ourselves to do it right.
            refs = self.upstream_sh.git("log", "--graph", "--oneline", "--branches=gh/*/*/head", "--decorate")
        return "".join(prs) + "Repository state:\n\n" + indent(strip_trailing_whitespace(refs), '    ') + "\n\n"

    # ------------------------------------------------------------------------- #

    def test_simple(self):
        print("####################")
        print("### test_simple")
        print("###")

        print("### First commit")
        self.sh.git("commit", "--allow-empty", "-m", "Commit 1\n\nThis is my first commit")
        self.sh.test_tick()
        self.gh()
        self.substituteRev("HEAD", "rCOM1")
        self.substituteRev("gh/ezyang/1/head", "rMRG1")
        self.assertExpected(self.dump_github(), '''\
#500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    This is my first commit

     * rMRG1 Commit 1

Repository state:

    * rMRG1 (gh/ezyang/1/head) Commit 1
    * rINI0 (HEAD -> master, gh/ezyang/1/base) Initial commit

''')
        print("###")
        print("### Second commit")
        self.sh.git("commit", "--allow-empty", "-m", "Commit 2\n\nThis is my second commit")
        self.sh.test_tick()
        self.gh()
        self.substituteRev("HEAD", "rCOM2")
        self.substituteRev("gh/ezyang/2/head", "rMRG2")
        self.assertExpected(self.dump_github(), '''\
#500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Commit 1

    This is my first commit

    Pull Request resolved: https://github.com/pytorch/pytorch/pull/500 (gh/ezyang/1/head)

     * rMRG1 Commit 1

#501 Commit 2 (gh/ezyang/2/head -> gh/ezyang/2/base)

    This is my second commit

     * rMRG2 Commit 2

Repository state:

    * rMRG2 (gh/ezyang/2/head) Commit 2
    * rMRG1 (gh/ezyang/2/base, gh/ezyang/1/head) Commit 1
    * rINI0 (HEAD -> master, gh/ezyang/1/base) Initial commit

''')

    # ------------------------------------------------------------------------- #

    def test_amend(self):
        print("####################")
        print("### test_amend")
        print("###")
        print("### First commit")
        self.sh.open("file1.txt", "w").write("A")
        self.sh.git("add", "file1.txt")
        self.sh.git("commit", "-m", "Commit 1\n\nA commit with an A")
        self.sh.test_tick()
        self.gh()
        self.substituteRev("HEAD", "rCOM1")
        self.substituteRev("gh/ezyang/1/head", "rMRG1")

        self.assertExpected(self.dump_github(), '''\
#500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    A commit with an A

     * rMRG1 Commit 1

Repository state:

    * rMRG1 (gh/ezyang/1/head) Commit 1
    * rINI0 (HEAD -> master, gh/ezyang/1/base) Initial commit

''')
        print("###")
        print("### Amend the commit")
        self.sh.open("file1.txt", "w").write("ABBA")
        self.sh.git("add", "file1.txt")
        # Can't use -m here, it will clobber the metadata
        self.sh.git("commit", "--amend")
        self.substituteRev("HEAD", "rCOM2")
        self.sh.test_tick()
        self.gh()
        self.substituteRev("gh/ezyang/1/head", "rMRG2")
        self.assertExpected(self.dump_github(), '''\
#500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Commit 1

    A commit with an A

    Pull Request resolved: https://github.com/pytorch/pytorch/pull/500 (gh/ezyang/1/head)

     * rMRG1 Commit 1
     * rMRG2 Update

Repository state:

    * rMRG2 (gh/ezyang/1/head) Update
    * rMRG1 Commit 1
    * rINI0 (HEAD -> master, gh/ezyang/1/base) Initial commit

''')

    # ------------------------------------------------------------------------- #

    def test_multi(self):
        print("####################")
        print("### test_multi")
        print("###")
        print("### First commit")
        self.sh.open("file1.txt", "w").write("A")
        self.sh.git("add", "file1.txt")
        self.sh.git("commit", "-m", "Commit 1\n\nA commit with an A")
        self.sh.test_tick()
        print("###")
        print("### Second commit")
        self.sh.open("file2.txt", "w").write("B")
        self.sh.git("add", "file2.txt")
        self.sh.git("commit", "-m", "Commit 2\n\nA commit with a B")
        self.sh.test_tick()

        self.gh()
        self.substituteRev("HEAD~", "rCOM1")
        self.substituteRev("HEAD", "rCOM2")
        self.substituteRev("gh/ezyang/1/head", "rMRG1")
        self.substituteRev("gh/ezyang/2/head", "rMRG2")

        self.assertExpected(self.dump_github(), '''\
#500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    A commit with an A

     * rMRG1 Commit 1

#501 Commit 2 (gh/ezyang/2/head -> gh/ezyang/2/base)

    A commit with a B

     * rMRG2 Commit 2

Repository state:

    * rMRG2 (gh/ezyang/2/head) Commit 2
    * rMRG1 (gh/ezyang/2/base, gh/ezyang/1/head) Commit 1
    * rINI0 (HEAD -> master, gh/ezyang/1/base) Initial commit

''')

    # ------------------------------------------------------------------------- #

    def test_amend_top(self):
        print("####################")
        print("### test_amend_top")
        print("###")
        print("### First commit")
        self.sh.open("file1.txt", "w").write("A")
        self.sh.git("add", "file1.txt")
        self.sh.git("commit", "-m", "Commit 1\n\nA commit with an A")
        self.sh.test_tick()
        self.gh()
        self.substituteRev("HEAD", "rCOM1")
        self.substituteRev("gh/ezyang/1/head", "rMRG1")

        print("###")
        print("### Second commit")
        self.sh.open("file2.txt", "w").write("B")
        self.sh.git("add", "file2.txt")
        self.sh.git("commit", "-m", "Commit 2\n\nA commit with a B")
        self.sh.test_tick()
        self.gh()
        self.substituteRev("HEAD", "rCOM2")
        self.substituteRev("gh/ezyang/2/head", "rMRG2")

        self.assertExpected(self.dump_github(), '''\
#500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Commit 1

    A commit with an A

    Pull Request resolved: https://github.com/pytorch/pytorch/pull/500 (gh/ezyang/1/head)

     * rMRG1 Commit 1

#501 Commit 2 (gh/ezyang/2/head -> gh/ezyang/2/base)

    A commit with a B

     * rMRG2 Commit 2

Repository state:

    * rMRG2 (gh/ezyang/2/head) Commit 2
    * rMRG1 (gh/ezyang/2/base, gh/ezyang/1/head) Commit 1
    * rINI0 (HEAD -> master, gh/ezyang/1/base) Initial commit

''')
        print("###")
        print("### Amend the top commit")
        self.sh.open("file2.txt", "w").write("BAAB")
        self.sh.git("add", "file2.txt")
        # Can't use -m here, it will clobber the metadata
        self.sh.git("commit", "--amend")
        self.substituteRev("HEAD", "rCOM2A")
        self.sh.test_tick()
        self.gh()
        self.substituteRev("gh/ezyang/2/head", "rMRG2A")
        self.assertExpected(self.dump_github(), '''\
#500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Commit 1

    A commit with an A

    Pull Request resolved: https://github.com/pytorch/pytorch/pull/500 (gh/ezyang/1/head)

     * rMRG1 Commit 1

#501 Commit 2 (gh/ezyang/2/head -> gh/ezyang/2/base)

    Commit 2

    A commit with a B

    Pull Request resolved: https://github.com/pytorch/pytorch/pull/501 (gh/ezyang/2/head)

     * rMRG2 Commit 2
     * rMRG2A Update

Repository state:

    * rMRG2A (gh/ezyang/2/head) Update
    * rMRG2 Commit 2
    * rMRG1 (gh/ezyang/2/base, gh/ezyang/1/head) Commit 1
    * rINI0 (HEAD -> master, gh/ezyang/1/base) Initial commit

''')

    # ------------------------------------------------------------------------- #

    def test_amend_bottom(self):
        print("####################")
        print("### test_amend_bottom")
        print("###")
        print("### First commit")
        self.sh.open("file1.txt", "w").write("A")
        self.sh.git("add", "file1.txt")
        self.sh.git("commit", "-m", "Commit 1\n\nA commit with an A")
        self.sh.test_tick()
        self.gh()
        self.substituteRev("HEAD", "rCOM1")
        self.substituteRev("gh/ezyang/1/head", "rMRG1")

        print("###")
        print("### Second commit")
        self.sh.open("file2.txt", "w").write("B")
        self.sh.git("add", "file2.txt")
        self.sh.git("commit", "-m", "Commit 2\n\nA commit with a B")
        self.sh.test_tick()
        self.gh()
        self.substituteRev("HEAD", "rCOM2")
        self.substituteRev("gh/ezyang/2/head", "rMRG2")

        self.assertExpected(self.dump_github(), '''\
#500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Commit 1

    A commit with an A

    Pull Request resolved: https://github.com/pytorch/pytorch/pull/500 (gh/ezyang/1/head)

     * rMRG1 Commit 1

#501 Commit 2 (gh/ezyang/2/head -> gh/ezyang/2/base)

    A commit with a B

     * rMRG2 Commit 2

Repository state:

    * rMRG2 (gh/ezyang/2/head) Commit 2
    * rMRG1 (gh/ezyang/2/base, gh/ezyang/1/head) Commit 1
    * rINI0 (HEAD -> master, gh/ezyang/1/base) Initial commit

''')

        print("###")
        print("### Amend the bottom commit")
        self.sh.git("checkout", "HEAD~")
        self.sh.open("file1.txt", "w").write("ABBA")
        self.sh.git("add", "file1.txt")
        # Can't use -m here, it will clobber the metadata
        self.sh.git("commit", "--amend")
        self.substituteRev("HEAD", "rCOM1A")
        self.sh.test_tick()
        self.gh()
        self.substituteRev("gh/ezyang/1/head", "rMRG1A")
        self.assertExpected(self.dump_github(), '''\
#500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Commit 1

    A commit with an A

    Pull Request resolved: https://github.com/pytorch/pytorch/pull/500 (gh/ezyang/1/head)

     * rMRG1 Commit 1
     * rMRG1A Update

#501 Commit 2 (gh/ezyang/2/head -> gh/ezyang/2/base)

    A commit with a B

     * rMRG2 Commit 2

Repository state:

    * rMRG1A (gh/ezyang/1/head) Update
    | * rMRG2 (gh/ezyang/2/head) Commit 2
    |/
    * rMRG1 (gh/ezyang/2/base) Commit 1
    * rINI0 (HEAD -> master, gh/ezyang/1/base) Initial commit

''')

        print("###")
        print("### Restack the top commit")
        self.sh.git("cherry-pick", self.lookupRev("rCOM2"))
        self.sh.test_tick()
        self.gh()
        self.substituteRev("HEAD", "rCOM2A")
        self.substituteRev("gh/ezyang/2/head", "rMRG2A")
        self.assertExpected(self.dump_github(), '''\
#500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Commit 1

    A commit with an A

    Pull Request resolved: https://github.com/pytorch/pytorch/pull/500 (gh/ezyang/1/head)

     * rMRG1 Commit 1
     * rMRG1A Update

#501 Commit 2 (gh/ezyang/2/head -> gh/ezyang/2/base)

    Commit 2

    A commit with a B

    Pull Request resolved: https://github.com/pytorch/pytorch/pull/501 (gh/ezyang/2/head)

     * rMRG2 Commit 2
     * rMRG2A Update

Repository state:

    *   rMRG2A (gh/ezyang/2/head) Update
    |\\
    | * rMRG1A (gh/ezyang/2/base, gh/ezyang/1/head) Update
    * | rMRG2 Commit 2
    |/
    * rMRG1 Commit 1
    * rINI0 (HEAD -> master, gh/ezyang/1/base) Initial commit

''')

    # ------------------------------------------------------------------------- #

    def test_amend_all(self):
        print("####################")
        print("### test_amend_all")
        print("###")
        print("### First commit")
        self.sh.open("file1.txt", "w").write("A")
        self.sh.git("add", "file1.txt")
        self.sh.git("commit", "-m", "Commit 1\n\nA commit with an A")
        self.sh.test_tick()
        self.gh()
        self.substituteRev("HEAD", "rCOM1")
        self.substituteRev("gh/ezyang/1/head", "rMRG1")

        print("###")
        print("### Second commit")
        self.sh.open("file2.txt", "w").write("B")
        self.sh.git("add", "file2.txt")
        self.sh.git("commit", "-m", "Commit 2\n\nA commit with a B")
        self.sh.test_tick()
        self.gh()
        self.substituteRev("HEAD", "rCOM2")
        self.substituteRev("gh/ezyang/2/head", "rMRG2")

        self.assertExpected(self.dump_github(), '''\
#500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Commit 1

    A commit with an A

    Pull Request resolved: https://github.com/pytorch/pytorch/pull/500 (gh/ezyang/1/head)

     * rMRG1 Commit 1

#501 Commit 2 (gh/ezyang/2/head -> gh/ezyang/2/base)

    A commit with a B

     * rMRG2 Commit 2

Repository state:

    * rMRG2 (gh/ezyang/2/head) Commit 2
    * rMRG1 (gh/ezyang/2/base, gh/ezyang/1/head) Commit 1
    * rINI0 (HEAD -> master, gh/ezyang/1/base) Initial commit

''')

        print("###")
        print("### Amend the commits")
        self.sh.git("checkout", "HEAD~")
        self.sh.open("file1.txt", "w").write("ABBA")
        self.sh.git("add", "file1.txt")
        # Can't use -m here, it will clobber the metadata
        self.sh.git("commit", "--amend")
        self.substituteRev("HEAD", "rCOM1A")
        self.sh.test_tick()

        self.sh.git("cherry-pick", self.lookupRev("rCOM2"))
        self.substituteRev("HEAD", "rCOM2A")
        self.sh.test_tick()

        self.gh()
        self.substituteRev("gh/ezyang/1/head", "rMRG1A")
        self.substituteRev("gh/ezyang/2/head", "rMRG2A")

        self.assertExpected(self.dump_github(), '''\
#500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Commit 1

    A commit with an A

    Pull Request resolved: https://github.com/pytorch/pytorch/pull/500 (gh/ezyang/1/head)

     * rMRG1 Commit 1
     * rMRG1A Update

#501 Commit 2 (gh/ezyang/2/head -> gh/ezyang/2/base)

    Commit 2

    A commit with a B

    Pull Request resolved: https://github.com/pytorch/pytorch/pull/501 (gh/ezyang/2/head)

     * rMRG2 Commit 2
     * rMRG2A Update

Repository state:

    *   rMRG2A (gh/ezyang/2/head) Update
    |\\
    | * rMRG1A (gh/ezyang/2/base, gh/ezyang/1/head) Update
    * | rMRG2 Commit 2
    |/
    * rMRG1 Commit 1
    * rINI0 (HEAD -> master, gh/ezyang/1/base) Initial commit

''')

    # ------------------------------------------------------------------------- #

    def test_rebase(self):
        print("####################")
        print("### test_rebase")

        self.sh.git("checkout", "-b", "feature")

        print("###")
        print("### First commit")
        self.sh.open("file1.txt", "w").write("A")
        self.sh.git("add", "file1.txt")
        self.sh.git("commit", "-m", "Commit 1\n\nA commit with an A")
        self.sh.test_tick()
        self.gh()
        self.substituteRev("HEAD", "rCOM1")
        self.substituteRev("gh/ezyang/1/head", "rMRG1")

        print("###")
        print("### Second commit")
        self.sh.open("file2.txt", "w").write("B")
        self.sh.git("add", "file2.txt")
        self.sh.git("commit", "-m", "Commit 2\n\nA commit with a B")
        self.sh.test_tick()
        self.gh()
        self.substituteRev("HEAD", "rCOM2")
        self.substituteRev("gh/ezyang/2/head", "rMRG2")

        self.assertExpected(self.dump_github(), '''\
#500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Commit 1

    A commit with an A

    Pull Request resolved: https://github.com/pytorch/pytorch/pull/500 (gh/ezyang/1/head)

     * rMRG1 Commit 1

#501 Commit 2 (gh/ezyang/2/head -> gh/ezyang/2/base)

    A commit with a B

     * rMRG2 Commit 2

Repository state:

    * rMRG2 (gh/ezyang/2/head) Commit 2
    * rMRG1 (gh/ezyang/2/base, gh/ezyang/1/head) Commit 1
    * rINI0 (HEAD -> master, gh/ezyang/1/base) Initial commit

''')

        print("###")
        print("### Push master forward")
        self.sh.git("checkout", "master")
        self.sh.open("master.txt", "w").write("M")
        self.sh.git("add", "master.txt")
        self.sh.git("commit", "-m", "Master commit 1\n\nA commit with a M")
        self.substituteRev("HEAD", "rINI2")
        self.sh.test_tick()
        self.sh.git("push", "origin", "master")

        print("###")
        print("### Rebase the commits")
        self.sh.git("checkout", "feature")
        self.sh.git("rebase", "origin/master")

        self.substituteRev("HEAD", "rCOM2A")
        self.substituteRev("HEAD~", "rCOM1A")

        self.gh()
        self.substituteRev("gh/ezyang/1/head", "rMRG1A")
        self.substituteRev("gh/ezyang/2/head", "rMRG2A")

        self.assertExpected(self.dump_github(), '''\
#500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Commit 1

    A commit with an A

    Pull Request resolved: https://github.com/pytorch/pytorch/pull/500 (gh/ezyang/1/head)

     * rMRG1 Commit 1
     * rMRG1A Update

#501 Commit 2 (gh/ezyang/2/head -> gh/ezyang/2/base)

    Commit 2

    A commit with a B

    Pull Request resolved: https://github.com/pytorch/pytorch/pull/501 (gh/ezyang/2/head)

     * rMRG2 Commit 2
     * rMRG2A Update

Repository state:

    *   rMRG2A (gh/ezyang/2/head) Update
    |\\
    | *   rMRG1A (gh/ezyang/2/base, gh/ezyang/1/head) Update
    | |\\
    | | * rINI2 (HEAD -> master, gh/ezyang/1/base) Master commit 1
    * | | rMRG2 Commit 2
    |/ /
    * | rMRG1 Commit 1
    |/
    * rINI0 Initial commit

''')

    # ------------------------------------------------------------------------- #

    def test_cherry_pick(self):
        print("####################")
        print("### test_cherry_pick")

        self.sh.git("checkout", "-b", "feature")

        print("###")
        print("### First commit")
        self.sh.open("file1.txt", "w").write("A")
        self.sh.git("add", "file1.txt")
        self.sh.git("commit", "-m", "Commit 1\n\nA commit with an A")
        self.sh.test_tick()
        self.gh()
        self.substituteRev("HEAD", "rCOM1")
        self.substituteRev("gh/ezyang/1/head", "rMRG1")

        print("###")
        print("### Second commit")
        self.sh.open("file2.txt", "w").write("B")
        self.sh.git("add", "file2.txt")
        self.sh.git("commit", "-m", "Commit 2\n\nA commit with a B")
        self.sh.test_tick()
        self.gh()
        self.substituteRev("HEAD", "rCOM2")
        self.substituteRev("gh/ezyang/2/head", "rMRG2")

        self.assertExpected(self.dump_github(), '''\
#500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Commit 1

    A commit with an A

    Pull Request resolved: https://github.com/pytorch/pytorch/pull/500 (gh/ezyang/1/head)

     * rMRG1 Commit 1

#501 Commit 2 (gh/ezyang/2/head -> gh/ezyang/2/base)

    A commit with a B

     * rMRG2 Commit 2

Repository state:

    * rMRG2 (gh/ezyang/2/head) Commit 2
    * rMRG1 (gh/ezyang/2/base, gh/ezyang/1/head) Commit 1
    * rINI0 (HEAD -> master, gh/ezyang/1/base) Initial commit

''')

        print("###")
        print("### Push master forward")
        self.sh.git("checkout", "master")
        self.sh.open("master.txt", "w").write("M")
        self.sh.git("add", "master.txt")
        self.sh.git("commit", "-m", "Master commit 1\n\nA commit with a M")
        self.substituteRev("HEAD", "rINI2")
        self.sh.test_tick()
        self.sh.git("push", "origin", "master")

        print("###")
        print("### Cherry-pick the second commit")
        self.sh.git("cherry-pick", "feature")

        self.substituteRev("HEAD", "rCOM2A")

        self.gh()
        self.substituteRev("gh/ezyang/2/head", "rMRG2A")

        self.assertExpected(self.dump_github(), '''\
#500 Commit 1 (gh/ezyang/1/head -> gh/ezyang/1/base)

    Commit 1

    A commit with an A

    Pull Request resolved: https://github.com/pytorch/pytorch/pull/500 (gh/ezyang/1/head)

     * rMRG1 Commit 1

#501 Commit 2 (gh/ezyang/2/head -> gh/ezyang/2/base)

    Commit 2

    A commit with a B

    Pull Request resolved: https://github.com/pytorch/pytorch/pull/501 (gh/ezyang/2/head)

     * rMRG2 Commit 2
     * rMRG2A Update

Repository state:

    *   rMRG2A (gh/ezyang/2/head) Update
    |\\
    | *   c3a8406 (gh/ezyang/2/base) Update base
    | |\\
    | | * rINI2 (HEAD -> master) Master commit 1
    * | | rMRG2 Commit 2
    |/ /
    * | rMRG1 (gh/ezyang/1/head) Commit 1
    |/
    * rINI0 (gh/ezyang/1/base) Initial commit

''')



#   def load_tests(loader, tests, ignore):
#       tests.addTests(doctest.DocTestSuite(gh))
#       return tests


if __name__ == '__main__':
    unittest.main()
