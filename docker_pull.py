# FROM: https://raw.githubusercontent.com/NotGlop/docker-drag/master/docker_pull.py

import os
import sys
import gzip
from io import BytesIO
import json
import hashlib
import shutil
import requests
import tarfile
import urllib3
urllib3.disable_warnings()

if len(sys.argv) != 2 :
	print('Usage:\n\tdocker_pull.py [repository/]image[:tag]\n')
	exit(1)

# Look for the Docker image to download
repo = 'library'
tag = 'latest'
try:
    repo,imgtag = sys.argv[1].split('/')
except ValueError:
    imgtag = sys.argv[1]
try:
    img,tag = imgtag.split(':')
except ValueError:
    img = imgtag
repository = f'{repo}/{img}'

# Get Docker token and fetch manifest v2
resp = requests.get(
	f'https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repository}:pull',
	verify=False,
)
access_token = resp.json()['access_token']
auth_head = {
	'Authorization': f'Bearer {access_token}',
	'Accept': 'application/vnd.docker.distribution.manifest.v2+json',
}

# Get image layer digests
resp = requests.get(
	f'https://registry-1.docker.io/v2/{repository}/manifests/{tag}',
	headers=auth_head,
	verify=False,
)
if (resp.status_code != 200):
	print(f'Cannot fetch manifest for {repository} [HTTP {resp.status_code}]')
	exit(1)
layers = resp.json()['layers']

# Create tmp folder that will hold the image
imgdir = f'tmp_{img}_{tag}'
os.mkdir(imgdir)
print(f'Creating image structure in: {imgdir}')

config = resp.json()['config']['digest']
confresp = requests.get(
	f'https://registry-1.docker.io/v2/{repository}/blobs/{config}',
	headers=auth_head,
	verify=False,
)
with open(f'{imgdir}/{config[7:]}.json', 'wb') as file:
	file.write(confresp.content)
content = [
	{
		'Config': f'{config[7:]}.json',
		'RepoTags': [f'{repository}:{tag}'],
		'Layers': [],
	}
]

empty_json = '{"created":"1970-01-01T00:00:00Z","container_config":{"Hostname":"","Domainname":"","User":"","AttachStdin":false, \
	"AttachStdout":false,"AttachStderr":false,"Tty":false,"OpenStdin":false, "StdinOnce":false,"Env":null,"Cmd":null,"Image":"", \
	"Volumes":null,"WorkingDir":"","Entrypoint":null,"OnBuild":null,"Labels":null}}'

# Build layer folders
parentid=''
for layer in layers:
	ublob = layer['digest']
	# FIXME: Creating fake layer ID. Don't know how Docker generates it
	fake_layerid = hashlib.sha256((parentid+'\n'+ublob+'\n').encode('utf-8')).hexdigest()
	layerdir = f'{imgdir}/{fake_layerid}'
	os.mkdir(layerdir)

	with open(f'{layerdir}/VERSION', 'w') as file:
		file.write('1.0')
	# Creating layer.tar file
	sys.stdout.write(f'{ublob[7:19]}: Downloading...')
	sys.stdout.flush()
	bresp = requests.get(
		f'https://registry-1.docker.io/v2/{repository}/blobs/{ublob}',
		headers=auth_head,
		verify=False,
	)
	if (bresp.status_code != 200):
		print(
			f'\rERROR: Cannot download layer {ublob[7:19]} [HTTP {bresp.status_code}]'
		)
		print(bresp.content)
		exit(1)
	print(f"\r{ublob[7:19]}: Pull complete [{bresp.headers['Content-Length']}]")
	content[0]['Layers'].append(f'{fake_layerid}/layer.tar')
	with open(f'{layerdir}/layer.tar', "wb") as file:
		mybuff = BytesIO(bresp.content)
		unzLayer = gzip.GzipFile(fileobj=mybuff)
		file.write(unzLayer.read())
		unzLayer.close()
	with open(f'{layerdir}/json', 'w') as file:
		# last layer = config manifest - history - rootfs
		if layers[-1]['digest'] == layer['digest']:
			# FIXME: json.loads() automatically converts to unicode, thus decoding values whereas Docker doesn't
			json_obj = json.loads(confresp.content)
			del json_obj['history']
			del json_obj['rootfs']
		else: # other layers json are empty
			json_obj = json.loads(empty_json)
		json_obj['id'] = fake_layerid
		if parentid:
			json_obj['parent'] = parentid
		parentid = json_obj['id']
		file.write(json.dumps(json_obj))
with open(f'{imgdir}/manifest.json', 'w') as file:
	file.write(json.dumps(content))
content = { repository : { tag : fake_layerid } }
with open(f'{imgdir}/repositories', 'w') as file:
	file.write(json.dumps(content))
# Create image tar and clean tmp folder
docker_tar = f'{repo}_{img}.tar'
tar = tarfile.open(docker_tar, "w")
tar.add(imgdir, arcname=os.path.sep)
tar.close()
shutil.rmtree(imgdir)
print(f'Docker image pulled: {docker_tar}')
