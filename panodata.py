#!/usr/bin/env python3

import argparse
import requests
import collections
import pprint
import itertools
import logging
import shutil
import os
import os.path

import json

import gpolyline
import geographiclib.geodesic

parser = argparse.ArgumentParser()
parser.add_argument('file', action='store',
                    help='Route file.')
args = parser.parse_args()

logging.basicConfig(level=logging.DEBUG)

latlng_urlstr = lambda latlng: "{},{}".format(*latlng)

logging.info('Loading file.')
with open(args.file, 'r') as f:
    data = json.load(f, object_pairs_hook=collections.OrderedDict)

try:
    if 'route_response' not in data:
        logging.info('Fetching route.')
        data['route_response'] = requests.get(
            'https://maps.googleapis.com/maps/api/directions/json',
            params={
                'origin': latlng_urlstr(data['route_request']['origin']),
                'destination': latlng_urlstr(data['route_request']['origin']),
                'waypoints': '|'.join(('via:{}'.format(latlng_urlstr(wp)) for wp in data['route_request']['waypoints'])),
                'sensor': 'false',
                'key': 'AIzaSyC74vPZz2tYpRuRWY7kZ8iaQ17Xam1-_-A',
            }).json()
    
    if 'points_pano' not in data:
        logging.info('Calculating pano retrival points.')
        steps = list(itertools.chain(*(leg['steps'] for leg in data['route_response']['routes'][0]['legs'])))
        step_points = (gpolyline.decode(step['polyline']['points']) for step in steps)
        points = list(itertools.chain(*(points if i == 0 else points[1:] for i, points in enumerate(step_points))))
        
        points_more = [points[0]]
        prev_point = points[0]    
        
        total_distance = 0
        for point in points[1:]:
            gd = geographiclib.geodesic.Geodesic.WGS84.Inverse(prev_point[0], prev_point[1], point[0], point[1])
            total_distance += gd['s12']
            line = geographiclib.geodesic.Geodesic.WGS84.Line(gd['lat1'], gd['lon1'], gd['azi1'])
            n_points = int(round(gd['s12']/4))
            for i in range(1, n_points):
                more_point = line.Position(gd['s12'] / n_points * i)
                points_more.append((round(more_point['lat2'],6), round(more_point['lon2'], 6)))
            points_more.append(point)
            prev_point = point
        
        data['points_pano'] = [(point[0], point[1], False) for point in points_more]
        logging.debug((len(points), len(points_more)))
    
    points_no_panos = [(i, point_pano)
                       for i, point_pano in enumerate(data['points_pano'])
                       if not point_pano[2]]
    
    pano_ids = set()
    if 'panos' in data:
        def dup_check(pano):
            if pano['id'] in pano_ids:
                return False
            pano_ids.add(pano['id'])
            return True
        
        data['panos'] = [pano for pano in data['panos'] if dup_check(pano)]
    else:
        data['panos'] = []
    panos = data['panos']
    
    if 'pano_links' not in data:
        data['pano_links'] = collections.OrderedDict()
    pano_links = data['pano_links']
    
    
    if points_no_panos:
        logging.info('Fetching pano data.')
        for i, point_pano in points_no_panos:
            pano_data = requests.get(
                'http://maps.google.com/cbk',
                params={
                    'output': 'json',
                    'hl': 'en',
                    'radius': 2,
                    'cb_client': 'maps_sv',
                    'v': 4,
                    'll': latlng_urlstr(point_pano),
                    'key': 'AIzaSyC74vPZz2tYpRuRWY7kZ8iaQ17Xam1-_-A',
                }).json()
            point_pano[2] = True
            if pano_data:
                location = pano_data['Location']
                point_pano[2] = location['panoId']
                if location['panoId'] not in pano_ids:
                    links = [link['panoId'] for link in pano_data['Links']]
                    pano = dict(id=location['panoId'], lat=float(location['lat']), lng=float(location['lng']),
                                description=location['description'], links=links, i=i, )
                    if 'elevation_wgs84_m' in location:
                        pano['elv']=float(location['elevation_wgs84_m'])
                    panos.append(pano)
                    pano_ids.add(location['panoId'])
                    for link in pano_data['Links']:
                        if link['panoId'] in pano_ids:
                            key = '-'.join((link['panoId'], location['panoId']))
                            pano_links[key] = round((float(link['yawDeg']) + 180) % 360, 2)
                            has_link = True
            
            
            if len(panos) == 300:
                break
    
    last_yaw = 0
    for i, pano in enumerate(panos[:-1]):
        next_pano = panos[i + 1]
        prev_pano = panos[i - 1]
        
        if next_pano['id'] not in pano['links'] and prev_pano['id'] not in pano['links']:
            pano['exclued'] = 1
        
        if 'exclued' not in pano:
            link_key = '-'.join((pano['id'], next_pano['id']))
            yaw = pano_links.get(link_key, last_yaw)
            last_yaw = yaw
            path = 'pano_img/{}-{}.jpeg'.format(pano['id'], yaw)
            
            if not os.path.exists(path):
                img = requests.get(
                    'http://maps.googleapis.com/maps/api/streetview',
                    params={
                        'size': '640x480',
                        'pano': pano['id'],
                        'fov': 110,
                        'heading': yaw,
                        'sensor': 'false',
                        'key': 'AIzaSyC74vPZz2tYpRuRWY7kZ8iaQ17Xam1-_-A',
                    },
                    stream=True,
                    )
                img.raise_for_status()
                
                with open(path, 'wb') as f:
                    shutil.copyfileobj(img.raw, f)
                del img
            sln_path = 'bynum/{:08d}.jpeg'.format(pano['i'])
            if not os.path.exists(sln_path):
                #os.symlink('../{}'.format(path), sln_path)
                os.link(path, sln_path)


except:
    logging.exception('')
finally:
    pass
    logging.info('Saving file.') 
    json_out = json.dumps(data, indent=2)
    with open(args.file, 'w') as f:
        f.write(json_out)
