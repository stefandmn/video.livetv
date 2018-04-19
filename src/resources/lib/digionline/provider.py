# -*- coding: utf-8 -*-

import os
import re
import sys
import time
import json
import urllib
import urllib2
import commons
import modshell
import cookielib
import HTMLParser
from xml.dom import minidom as xml
from .exceptions import LoginException, DigiOnlineException
from modshell.items.DirectoryItem import DirectoryItem
from modshell.items.VideoItem import VideoItem

if hasattr(sys.modules["__main__"], "xbmc"):
	xbmc = sys.modules["__main__"].xbmc
else:
	import xbmc

if hasattr(sys.modules["__main__"], "xbmcvfs"):
	xbmcvfs = sys.modules["__main__"].xbmcvfs
else:
	import xbmcvfs


class Provider(modshell.AbstractProvider):

	def __init__(self):
		self._source = xml.parse(os.path.join(commons.AddonPath(), 'resources', 'sources.xml'))
		self._cookie = None
		self._appname = "Kodi"
		self._apptype = "pcbrowser"
		self._appversion = "17.6"
		self._osname = "Raspbian"
		self._ostype = "Linux"
		self._osversion = "7"
		self._serial = "123806a4cf23e251087b9da0892b100A"
		modshell.AbstractProvider.__init__(self)
		pass

	def onRoot(self, context, re_match):
		folders = []
		for folder in self._source.getElementsByTagName('category'):
			category = DirectoryItem(folder.getAttribute("label"), context.createUri(['category', folder.getAttribute("id")]),
				image=context.createResourcePath('media', folder.getAttribute("icon")))
			folders.append(category)
		return folders

	@modshell.RegisterProviderPath('^/category/(?P<category>.*)/$')
	def onCategory(self, context, re_match):
		channels = []
		category = re_match.group('category')
		for folder in self._source.getElementsByTagName('category'):
			if folder.getAttribute("id") == category:
				for item in folder.getElementsByTagName('channel-v1'):
					channel = VideoItem(item.getAttribute("label"), context.createUri(['play'], {"version":"1", "channel":item.getAttribute("id")}), image=context.createResourcePath('media', item.getAttribute("icon")))
					channels.append(channel)
				for item in folder.getElementsByTagName('channel-v2'):
					channel = VideoItem(item.getAttribute("label"), context.createUri(['play'], {"version":"2", "channel":item.getAttribute("id")}), image=context.createResourcePath('media', item.getAttribute("icon")))
					channels.append(channel)
				for item in folder.getElementsByTagName('channel-v3'):
					channel = VideoItem(item.getAttribute("label"), context.createUri(['play'], {"version":"3", "channel":item.getAttribute("id")}), image=context.createResourcePath('media', item.getAttribute("icon")))
					channels.append(channel)
				break
		return channels

	@modshell.RegisterProviderPath('^/play/$')
	def onPlay(self, context, re_match):
		params = context.getParams()
		version = commons.any2int(params["version"])
		channel = self.getChannel(version, params["channel"])
		# 1. Get channel
		if version == 1:
			channel = self.getChannelV1(channel)
		else:
			raise DigiOnlineException("Channels v2 or v3 are not implemented yet, stay tuned for the next updates!")
		# 2. Display info
		if commons.setting('digionline.playback.epginfo') and channel.get("plot") is not None:
			if channel["label"] is None or channel["label"] == "":
				xbmc.executebuiltin("Notification(Digi-Online, " + channel["plot"] + ", 10000)")
			else:
				xbmc.executebuiltin("Notification(" + channel["label"] + ", " + channel["plot"] + ", 10000)")
		# 3. Play video
		commons.debug("Preparing to play video URL: %s" %channel["url"])
		self._play(context, channel)

	def getChannel(self, version, channel):
		for folder in self._source.getElementsByTagName('category'):
			for item in folder.getElementsByTagName('channel-v'+str(version)):
				if item.getAttribute("id") == channel:
					return {"id":item.getAttribute("id"),
							"version":commons.any2int(version),
							"label":item.getAttribute("label"),
							"auth":item.getAttribute("auth"),
							"url":item.getAttribute("url"),
							"icon":item.getAttribute("icon")}

	def getChannelV1(self, channel):
		# 0. Initialize browser
		browser = urllib2.build_opener(urllib2.HTTPCookieProcessor(self.getCookie()))
		commons.debug("Initializing browser using cookie: %s" %str(self._cookie))
		# 1. Run data collection process: epg info, channel scope and master key
		try:
			url = HTMLParser.HTMLParser().unescape(channel["url"])
			commons.debug("Executing HTTP GET call to collect channel data: %s" %url)
			httpget = browser.open(url)
			content = httpget.read()
		except:
			content = ''
			pass
		# 1.1 Identify epg info
		if content is not None and '<div class="info" epg-data=' in content:
			seps = ["[", "]", "'", "{", "}", "start:", "stop:", "title:"]
			tagdata = str(re.compile('<div class="info" epg-data="(.+?)"').findall(content)).replace("&quot;", "")
			for i in range(len(seps)):
				tagdata = tagdata.replace(str(seps[i]), "")
			if len(tagdata) > 0:
				parts = tagdata.split(',')
				nowinfo = None
				nextinfo = None
				try:
					nowinfo = commons.translate(30010) + ": " + time.strftime("%H:%M", time.localtime(int(parts[1]))) + " - " + time.strftime("%H:%M", time.localtime(int(parts[2]))) + " " + str(parts[0])
					nextinfo = commons.translate(30011) + ": " + time.strftime("%H:%M", time.localtime(int(parts[4]))) + " - " + time.strftime("%H:%M", time.localtime(int(parts[5]))) + " " + str(parts[3])
				except:
					pass
				if nowinfo is not None and nextinfo is not None:
					channel["plot"] = nowinfo + " | " + nextinfo
				elif nowinfo is not None and nextinfo is None:
					channel["plot"] = nowinfo
			if channel.get("plot") is not None:
				commons.debug("Added [plot] property to '%s' channel: %s" %(channel["id"], channel["plot"]))
		# 1.2 Identify channel scope
		match = re.compile('data-balancer-scope-name="(.+?)"').findall(content)
		if len(match) > 0:
			channel["scope"] = str(match[0]).strip()
		else:
			channel["scope"] = channel["id"]
		commons.debug("Added [scope] property to '%s' channel: %s" %(channel["id"], channel["scope"]))
		# 1.3 Identify master key
		match = re.compile('data-balancer-key="(.+?)"').findall(content)
		if len(match) > 0:
			channel["key"] = str(match[0]).strip()
			commons.debug("Added [key] property to '%s' channel: %s" %(channel["id"], channel["key"]))
		# 2. Run Authentication process for configured account
		if commons.any2bool(channel["auth"]) and commons.setting("digionline.login.enabled"):
			url = "http://www.digi-online.ro/xhr-login.php"
			browser.addheaders = [('Host', "www.digi-online.ro"), ('Accept', '*/*'), ('Origin', "http://www.digi-online.ro"),
							('X-Requested-With', 'XMLHttpRequest'), ('User-Agent', self.getAgent()), ('Content-type', 'application/x-www-form-urlencoded'),
							('Referer', "http://www.digi-online.ro"), ('Accept-Encoding', 'identity'), ('Accept-Language', 'en-ie'), ('Connection', 'close')]
			formdata = urllib.urlencode({'user': commons.getSetting('digionline.login.username'),
								'password': commons.getSetting('digionline.login.password'),
								'browser': self._appname, 'model': self._appversion, 'os': self._osname})
			try:
				commons.debug("Executing HTTP POST for authentication: %s, form data: %s" %(url, formdata))
				httppost = browser.open(url, formdata)
				response = httppost.read()
				commons.debug("Received HTTP POST answer: %s" %response)
			except:
				raise LoginException(commons.translate(30051))
			if commons.any2bool(response):
				for cookie in self._cookie:
					if str(cookie.name) == 'sid':
						channel["sid"] = str(cookie.value).strip()
						commons.debug("Added [sid] property to '%s' channel: %s" %(channel["id"], channel["sid"]))
			else:
				raise LoginException(commons.translate(30050))
		elif commons.any2bool(channel["auth"]) and not commons.setting("digionline.login.enabled"):
			raise LoginException(commons.translate(30052))
		# 3.1 Execute authorization to access resources
		url = 'http://www.digi-online.ro/xhr-gen-stream.php'
		browser.addheaders = [('X-Requested-With', 'XMLHttpRequest')]
		formdata = urllib.urlencode({'scope':channel["scope"]})
		try:
			commons.debug("Executing HTTP POST call to authorize the scope: %s, form data: %s" %(url, formdata))
			httppost = browser.open(url, formdata)
			response = httppost.read()
			commons.debug("Received HTTP GET answer: %s" %response)
		except:
			pass
		# 3.2 Generates master key if is not already detected
		if channel.get("key") is None:
			url = "http://balancer.digi24.ro/streamer/make_key.php"
			browser.addheaders = [('Host', "balancer.digi24.ro"), ('Accept', '*/*'), ('Origin', "http://www.digi-online.ro"),
							('User-Agent', self.getAgent()), ('Referer', channel["url"]), ('Accept-Encoding', 'identity'),
							('Accept-Language', 'en-GB,en;q=0.5'), ('Connection', 'close')]
			try:
				commons.debug("Executing HTTP GET call to get master key: %s" %url)
				httpget = browser.open(url)
				content = httpget.read()
				commons.debug("Received HTTP GET answer: %s" %content)
			except:
				content = ''
				pass
			if len(content) > 0:
				channel["key"] = str(content)
				commons.debug("Added [key] property to '%s' channel: %s" %(channel["id"], channel["key"]))
		# 3.3 Get video information
		url = 'http://balancer.digi24.ro/streamer.php?&scope=' + channel["scope"] + '&key=' + channel["key"] + '&outputFormat=json&type=hls&quality=' + commons.getSetting("digionline.playback.quality")
		try:
			commons.debug("Executing HTTP GET call to obtain video information: %s" %url)
			content = browser.open(url).read()
			commons.debug("Received HTTP GET answer: %s" %content)
		except:
			raise DigiOnlineException(commons.translate(30053))
		data = json.loads(content)
		if data.get("file") is not None and "http:" not in data["file"]:
			channel["url"] = "http:" + data["file"]
		elif data.get("file") is not None and "http:" not in data["file"]:
			channel["url"] = data["file"]
		else:
			raise DigiOnlineException(commons.translate(30054))
		commons.debug("Updated [url] property to '%s' channel: %s" %(channel["id"], channel["url"]))
		return channel

	def _play(self, context, channel):
		item = VideoItem(channel["label"], channel["url"], channel["icon"])
		item.setMediatype('video')
		item.setGenre('Live Stream')
		item.setPlot(channel["plot"]) if channel.get("plot") is not None else item.setPlot("")
		return item

	def getDevice(self):
		return self._appname.lower() + "_" + self._ostype.lower() + "_" + self._serial + "_" + self._apptype.lower()

	def getAgent(self):
		return "Mozilla/5.0 (" + self._osname + " " + self._osversion + "; " + self._ostype + ") Gecko/20100101 " + self._appname + "/" + self._appversion

	def getCookie(self):
		if self._cookie is None:
			self._create_cookie("device_id", self.getDevice(), "www.digi-online.ro")
		return self._cookie

	def _create_cookie(self, name, value, domain):
		if self._cookie is None:
			cookiefile = xbmc.translatePath('special://profile/addon_data/%s/cookie' % commons.AddonId()).decode('utf-8')
			if xbmcvfs.exists(cookiefile):
				self._cookie = cookielib.MozillaCookieJar()
				self._cookie.load(cookiefile, ignore_discard=True)
				commons.debug("Loaded cookie %s from file: %s" %(str(self._cookie), cookiefile))
			else:
				self._cookie = cookielib.MozillaCookieJar()
				self._cookie.set_cookie(cookielib.Cookie(version=0, name=name, value=value, port=None,
					port_specified=False, domain=domain, domain_specified=True,
					domain_initial_dot=False, path="/", path_specified=True,
					secure=False, expires=None, discard=False, comment=None,
					comment_url=None, rest=None))
				self._cookie.save(cookiefile)
				commons.debug("Created cookie %s and saved to file: %s" %(str(self._cookie), cookiefile))

	@modshell.RegisterProviderPath('^/config/$')
	def onConfigureAddon(self, context, re_match):
			context._addon.openSettings()

	def handleException(self, context, exception_to_handle):
		if isinstance(exception_to_handle, LoginException):
			context.getAccessManager().updateAccessToken('')
			title = '%s: %s' % (context.getName(), 'LoginException')
			context.getUI().showNotification(exception_to_handle.message, title)
			context.error('%s: %s' % (title, exception_to_handle.message))
			context.getUI().openSettings()
			return False
		return True
