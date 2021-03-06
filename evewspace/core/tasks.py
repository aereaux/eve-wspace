#   Eve W-Space
#   Copyright 2014 Andrew Austin and contributors
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
from celery import task
from django.core.cache import cache
import urllib.request, urllib.parse, urllib.error
import json
from .models import Alliance, Corporation, NewsFeed
from API import cache_handler as handler
import eveapi
import feedparser

@task()
def update_alliance(allianceID):
    """
    Updates an alliance and it's corporations from the API.
    """
    api = eveapi.EVEAPIConnection(cacheHandler=handler)
    allianceapi = api.eve.AllianceList().alliances.Get(allianceID)
    if Alliance.objects.filter(id=allianceID).count():
        # Alliance exists, update it
        for corp in allianceapi.memberCorporations:
            try:
                update_corporation(corp.corporationID)
            except AttributeError:
                # Pass on this exception because one Russian corp has an
                # unavoidable bad character in their description
                pass
        alliance = Alliance.objects.get(id=allianceID)
        alliance.name = allianceapi.name
        alliance.shortname = allianceapi.shortName
        # Check to see if we have a record for the executor
        if Corporation.objects.filter(id=allianceapi.executorCorpID).count():
            alliance.executor = Corporation.objects.get(id=allianceapi.executorCorpID)
    else:
        # Alliance doesn't exists, add it without executor, update corps
        # and then update the executor
        alliance = Alliance(id=allianceapi.allianceID, name=allianceapi.name,
                shortname=allianceapi.shortName, executor=None)
        alliance.save()
        for corp in allianceapi.memberCorporations:
            try:
                update_corporation(corp.corporationID)
            except AttributeError:
                # Fuck you, xCAPITALSx
                pass
        try:
            # If an alliance's executor can't be processed for some reason,
            # set it to None
            alliance.executor = Corporation.objects.get(id=allianceapi.executorCorpID)
        except Exception:
            alliance.executor = None
        alliance.save()

@task()
def update_corporation(corpID, sync=False):
    """
    Updates a corporation from the API. If it's alliance doesn't exist,
    update that as well.
    """
    api = eveapi.EVEAPIConnection(cacheHandler=handler)
    # Encapsulate this in a try block because one corp has a fucked
    # up character that chokes eveapi
    try:
        corpapi = api.corp.CorporationSheet(corporationID=corpID)
    except Exception:
        raise AttributeError("Invalid Corp ID or Corp has malformed data.")

    if corpapi.allianceID:
        try:
            alliance = Alliance.objects.get(id=corpapi.allianceID)
        except Exception:
            # If the alliance doesn't exist, we start a task to add it
            # and terminate this task since the alliance task will call
            # it after creating the alliance object
            if not sync:
                update_alliance.delay(corpapi.allianceID)
                return
            else:
                # Something is waiting and requires the corp object
                # We set alliance to None and kick off the
                # update_alliance task to fix it later
                alliance = None
                update_alliance.delay(corpapi.allianceID)
    else:
        alliance = None

    if Corporation.objects.filter(id=corpID).exists():
        # Corp exists, update it
        corp = Corporation.objects.get(id=corpID)
        corp.member_count = corpapi.memberCount
        corp.ticker = corpapi.ticker
        corp.name = corpapi.corporationName
        corp.alliance = alliance
        corp.save()
    else:
        # Corp doesn't exist, create it
        corp = Corporation(id=corpID, member_count=corpapi.memberCount,
                name=corpapi.corporationName, alliance=alliance)
        corp.save()
    return corp

@task()
def update_all_alliances():
    """
    Updates all corps in all alliances. This task will take a long time
    to run.
    """
    api = eveapi.EVEAPIConnection(cacheHandler=handler)
    alliancelist = api.eve.AllianceList()
    for alliance in alliancelist.alliances:
        update_alliance(alliance.allianceID)


@task()
def cache_eve_reddit():
    """
    Attempts to cache the top submissions to r/Eve.
    """
    current = cache.get('reddit')
    if not current:
        # No reddit data is cached, grab it.
        data = json.loads(urllib.request.urlopen('http://www.reddit.com/r/Eve/hot.json').read())
        cache.set('reddit', data, 120)
    else:
        # There is cached data, let's try to update it
        data = json.loads(urllib.request.urlopen('http://www.reddit.com/r/Eve/hot.json').read())
        if 'data' in data:
            # Got valid response, store it
            cache.set('reddit', data, 120)
        else:
            # Invalid response, refresh current data
            cache.set('reddit', current, 120)

@task
def update_feeds():
    """
    Caches and updates RSS feeds in NewsFeeds.
    """
    for feed in NewsFeed.objects.all():
        try:
            data = feedparser.parse(feed.url)
        except Exception:
            data = 'error'
        cache.set('feed_%s' % feed.pk, data, 7200)
        feed.name = data['feed']['title']
        try:
            feed.description = data['feed']['subtitle']
        except KeyError:
            feed.decription = "None Provided"
        feed.save()
