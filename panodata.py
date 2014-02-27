#!/usr/bin/env python3
import argparse
import csv
import collections
import functools
import itertools
import json
import math
import logging
import os
import os.path
import requests
import shutil
import signal
import pprint

import geographiclib.geodesic
import gpolyline
import yaml

import video

geodesic = geographiclib.geodesic.Geodesic.WGS84

def dict_representer(dumper, data):
    return dumper.represent_mapping(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, data.items())


def dict_constructor(loader, node):
    return collections.OrderedDict(loader.construct_pairs(node))

yaml.add_representer(collections.OrderedDict, dict_representer)
yaml.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, dict_constructor)

def xfrange(start, stop=None, step=None):
    """Like range(), but returns list of floats instead

    All numbers are generated on-demand using generators
    """
    if stop is None:
        stop = float(start)
        start = 0.0
    if step is None:
        step = 1.0
    cur = float(start)
    while cur < stop:
        yield cur
        cur += step

def json_dump_list(l, f):
    f.write('[\n')
    for i, item in enumerate(l):
        f.write('  ')
        json.dump(item, f, sort_keys=True)
        if i == len(l) - 1:
            f.write('\n')
        else:
            f.write(',\n')
    f.write(']\n')
    
class DelayedKeyboardInterrupt(object):
    def __enter__(self):
        self.signal_received = False
        self.old_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self.handler)
    
    def handler(self, signal, frame):
        self.signal_received = (signal, frame)
        logging.debug('SIGINT received. Delaying KeyboardInterrupt.')
    
    def __exit__(self, type, value, traceback):
        signal.signal(signal.SIGINT, self.old_handler)
        if self.signal_received:
            self.old_handler(*self.signal_received)

try:
    
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', action='store', help='Route directory. This should contain atleast `source.yaml`.')
    parser.add_argument('--debug', action='store_true', help='Output DEBUG messages.')
    
    args = parser.parse_args()
    dir_join = functools.partial(os.path.join, args.directory)
    
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    logging.getLogger('requests').level = logging.ERROR
    
    latlng_urlstr = lambda latlng: "{},{}".format(*latlng)
    
    logging.info('Loading source.yaml')
    with open(dir_join('source.yaml'), 'r') as f:
        source = yaml.load(f)


    if os.path.exists(dir_join('route.json')):
        logging.info('loading route.json')
        with open(dir_join('route.json'), 'r') as f:
            route = json.load(f)
    else:
        logging.info('Fetching route')
        waypoints = '|'.join(('via:{}'.format(latlng_urlstr(wp)) for wp in source['route_request']['waypoints']))
        route = requests.get(
            'https://maps.googleapis.com/maps/api/directions/json',
            params={
                'origin': latlng_urlstr(source['route_request']['origin']),
                'destination': latlng_urlstr(source['route_request']['destination']),
                'waypoints': waypoints,
                'sensor': 'false',
                'key': 'AIzaSyC74vPZz2tYpRuRWY7kZ8iaQ17Xam1-_-A',
            }).json()
        logging.info('Saving route.json')
        with open(dir_join('route.json'), 'w') as f:
            json.dump(route, f, indent=2)

    steps = list(itertools.chain(*(leg['steps'] for leg in route['routes'][0]['legs'])))
    step_points = (gpolyline.decode(step['polyline']['points']) for step in steps)
    points = list(itertools.chain(*(points if i == 0 else points[1:] for i, points in enumerate(step_points))))
    with DelayedKeyboardInterrupt():
        logging.info('Saving route_points.json')
        with open(dir_join('route_points.json'), 'w') as f:
            json_dump_list(points, f)


    ################################################################################################################
    logging.info('Calculating additional route points.')
    points_more = [points[0]]
    prev_point = points[0]

    for point in points[1:]:
        gd = geodesic.Inverse(prev_point[0], prev_point[1], point[0], point[1])
        line = geodesic.Line(gd['lat1'], gd['lon1'], gd['azi1'])
        n_points = int(round(gd['s12'] / 1))
        for i in range(1, n_points):
            more_point = line.Position(gd['s12'] / n_points * i)
            points_more.append((round(more_point['lat2'], 6), round(more_point['lon2'], 6)))
        points_more.append(point)
        prev_point = point

    logging.debug((len(points), len(points_more)))
    points_indexed = [(lat, lng, i) for i, (lat, lng) in enumerate(points_more)]

    def get_closest_point(lat, lng, last_point):
        smallest_dist = None
        closest_point = None
        for point in points_indexed[last_point[2]:]:
            dist = geodesic.Inverse(point[0], point[1], lat, lng)['s12']
            if smallest_dist is None or dist < smallest_dist:
                smallest_dist = dist
                closest_point = point
            if dist > smallest_dist and dist >= 20 and smallest_dist < 20:
                break
        return (smallest_dist, closest_point)

    ################################################################################################################
    if os.path.exists(dir_join('panos.json')):
        logging.info('loading panos.json')
        with open(dir_join('panos.json'), 'r') as f:
            panos = json.load(f)
    else:
        panos = []
    
    try:
        pano_ids = set([pano['id'] for pano in panos])
        prefered_pano_chain = source.get('prefered_pano_chain', {})
        exculded_panos = set(source.get('exculded_panos', []))
        
        try:
            logging.info('Fetching pano data.')
            if panos:
                last_pano = panos[-1]
                last_point = points_indexed[last_pano['i']]
            else:
                last_pano = None
                last_point = points_indexed[0]
    
            while True:
                #if len(panos) >= 500:
                #    break
                if last_pano is None:
                    for point in points_indexed[last_point[2]:]:
                        logging.debug('Get for ({},{}) {}'.format(*point))
                        pano_data = requests.get(
                            'http://cbks0.googleapis.com/cbk',
                            params={
                                'output': 'json',
                                'radius': 3,
                                'll': latlng_urlstr(point),
                                'key': 'AIzaSC74vPZz2tYpRuRWY7kZ8iaQ17Xam1-_-A',
                            }).json()
                        if pano_data:
                            break
                    else:
                        break
                else:
                    if last_pano['id'] in prefered_pano_chain:
                        link_pano_id = prefered_pano_chain[last_pano['id']]
                    else:
                        if last_point[2] == len(points_indexed) -1 :
                            break
                        next_point = points_indexed[last_point[2] + 1]
                        yaw_to_next = geodesic.Inverse(last_point[0], last_point[1],
                                                       next_point[0], next_point[1])['azi1'] % 360
                        yaw_diff = lambda item: (abs(item['yaw'] - yaw_to_next)) % 360
                        pano_link = min(last_pano['links'], key=yaw_diff)
            
                        if yaw_diff(pano_link) > 20:
                            logging.debug("Yaw too different: {} {} {}".format(yaw_diff(pano_link), pano_link['yaw'], yaw_to_next))
                            link_pano_id = None
                        else:
                            link_pano_id = pano_link['panoId']
            
                    if link_pano_id:
                        #logging.debug('Get for {}'.format(link_pano_id))
                        pano_data = requests.get(
                            'http://cbks0.googleapis.com/cbk',
                            params={
                                'output': 'json',
                                'panoid': link_pano_id,
                                'key': 'AIzaSyC74vPZz2tYpRuRWY7kZ8iaQ17Xam1-_-A',
                            }).json()
                    else:
                        last_pano = None
                        pano_data = None
        
                if pano_data:
                    location = pano_data['Location']
                    pano_lat = float(location['lat'])
                    pano_lng = float(location['lng'])
                    if location['panoId'] not in pano_ids:
                        smallest_dist, closest_point = get_closest_point(pano_lat, pano_lng, last_point)
        
                        if smallest_dist > 20:
                            logging.debug("Distance {} to nearest point too great for pano: {}"
                                          .format(smallest_dist, location['panoId']))
                            last_pano = None
                        else:
                            last_point = closest_point
        
                            links = [dict(panoId=link['panoId'], yaw=float(link['yawDeg']))
                                     for link in pano_data['Links']]
                            pano = dict(
                                id=location['panoId'], lat=pano_lat, lng=pano_lng,
                                description=location['description'], links=links, i=last_point[2], )
                            if 'elevation_wgs84_m' in location:
                                pano['elv'] = float(location['elevation_wgs84_m'])
                            panos.append(pano)
                            pano_ids.add(location['panoId'])
                            last_pano = pano
                            logging.info("{description} ({lat},{lng}) {i}".format(**pano))
                    else:
                        last_point = points_indexed[last_point[2] + 1]
                        last_pano = None
        except KeyboardInterrupt:
            logging.error('KeyboardInterrupt')
        
        ################################################################################################################
        
        filtered_panos = [p for p in panos if p['id'] not in exculded_panos]
        
        for i, pano in enumerate(filtered_panos):
            pano['if'] = i
        
        
        logging.info("Inserting points into the gaps.")
        
        panos_with_missing = []
        for pano, next_pano in zip(filtered_panos[:-1], filtered_panos[1:]):
            panos_with_missing.append(pano)
            gd = geodesic.Inverse(pano['lat'], pano['lng'], next_pano['lat'], next_pano['lng'])
            if gd['s12'] > 20:
                n_points = round((next_pano['i'] - pano['i']) / 10)
                step = (next_pano['i'] - pano['i']) / n_points
                for i in [round(i) for i in xfrange(pano['i'] + step, next_pano['i'], step)]:
                    lat, lng, _ = points_indexed[i]
                    panos_with_missing.append(dict(lat=lat, lng=lng, i=i))
                pano['misssing_added'] = n_points
        panos_with_missing.append(next_pano)

        for pano in panos_with_missing:
            point = points_indexed[pano['i']]
            pano['point_lat'], pano['point_lng'] = point[0], point[1]

        panos_without_elevation = [pano for pano in panos_with_missing if 'elv2' not in pano]
        
        if panos_without_elevation:
            logging.info("Fetching elevation")
            n = 256
            for i in range(0, len(panos_without_elevation), n):
                polyline = gpolyline.encode_coords([(pano['point_lat'], pano['point_lng']) for pano in panos_without_elevation[i:i+n]])
                elevations = requests.get(
                    'https://maps.googleapis.com/maps/api/elevation/json',
                    params={
                        'sensor': 'false',
                        'key': 'AIzaSyC74vPZz2tYpRuRWY7kZ8iaQ17Xam1-_-A',
                        'locations':  "enc:{}".format(polyline)
                    })
                try:
                    elevations=elevations.json()
                except Exception as e:
                    logging.error("{} {}".format(e, elevations.text))
                for elv, pano in zip(elevations['results'], panos_without_elevation[i:i+n]):
                    pano['elv2'] = elv['elevation']

        logging.info("Calculating yaws, yaw_deltas, and grads")

        def deg_wrap_to_closest(deg, to_deg):
            up = deg + 360
            down = deg - 360
            return min(deg, up, down, key=lambda x: abs(to_deg - x))
        
        for pano, next_pano in zip(panos_with_missing[:-1], panos_with_missing[1:]):
            gd = geodesic.Inverse(pano['lat'], pano['lng'], next_pano['lat'], next_pano['lng'])
            pano['yaw'] = round(gd['azi1'] % 360, 4)
            pano['dist'] = gd['s12']
        next_pano['yaw'] = round(gd['azi2'] % 360, 4)
        next_pano['dist'] = gd['s12']
        
        def smooth(l, n, z, get_f, set_f):
            smooth_matrix = list(range(z, n + z)) + list(range(n + z - 1, z - 1, -1))
            matrix_sum = sum(smooth_matrix)
            product = lambda item: item[0] * item[1]
            l_len = len(l)
            for i in range(l_len):
                set_item = l[i]
                smooth_items = (l[min(max(j, 0), l_len - 1)] for j in range(i - n + 1, i + n +1))
                smooth_values = [get_f(item, set_item) for item in smooth_items]
                value = sum(map(product, zip(smooth_values, smooth_matrix))) / matrix_sum
                set_f(set_item, value)
        
        smooth(panos_with_missing, 8, 1,
               lambda item, set_item: deg_wrap_to_closest(item['yaw'], set_item['yaw']),
               lambda item, value: item.__setitem__('smooth_yaw', round(value % 360, 2)))

        smooth(panos_with_missing, 40, 35,
               lambda item, set_item: item['elv2'],
               lambda item, value: item.__setitem__('smooth_elv2', round(value, 2)))

        for pano, next_pano in zip(panos_with_missing[:-1], panos_with_missing[1:]):
            pano['yaw_delta'] = round(pano['yaw'] - deg_wrap_to_closest(next_pano['yaw'], pano['yaw']), 4)
            pano['grad'] = round((pano['smooth_elv2'] - next_pano['smooth_elv2'])/pano['dist'] * 4, 4)
        next_pano['yaw_delta'] = 0
        next_pano['grad'] = 0

        smooth(panos_with_missing, 20, 10,
               lambda item, set_item: item['yaw_delta'],
               lambda item, value: item.__setitem__('smooth_yaw_delta', round(value, 4)))
        
        smooth(panos_with_missing, 40, 30,
               lambda item, set_item: item['grad'],
               lambda item, value: item.__setitem__('smooth_grad', round(value, 4)))
        
        for pano in panos_with_missing:
            pano['yaw'] = round(pano['yaw'] % 360, 2)
            pano['yaw_delta_factor'] = 0 - pow(abs(pano['smooth_yaw_delta']), 2) / 20
        
        smooth(panos_with_missing, 40, 30,
               lambda item, set_item: item['yaw_delta_factor'],
               lambda item, value: item.__setitem__('smooth_yaw_delta_factor', round(value, 4)))
        
        for pano in panos_with_missing:
            pano['speed'] = 1 + pano['smooth_yaw_delta_factor'] + pano['smooth_grad']

    finally:
        with DelayedKeyboardInterrupt():
            logging.info('Saving panos.json')
            with open(dir_join('panos.json'), 'w') as f:
                json_dump_list(panos, f)
            
            point_debug = [(pano['lat'], pano['lng'], '{} - {}'.format(pano['i'], i))
                           for i, pano in list(enumerate(filtered_panos))[6000:]]
            logging.info('Saving point_debug.json')
            with open(dir_join('point_debug.json'), 'w') as f:
                json_dump_list(point_debug, f)
            
            logging.info('Saving points.csv')
            with open(dir_join('points.csv'), 'w') as f:
                w = csv.writer(f)
                
                #fields = ['i', 'i', 'id', 'yaw', 'smooth_yaw', 'dist', 'elv', 'smooth_elv2', 'smooth_grad', 'smooth_yaw_delta']
                #fields = ['i', 'i', 'smooth_yaw', 'smooth_yaw_delta', 'smooth_yaw_delta_factor']
                fields = ['i', 'i', 'smooth_yaw_delta', 'smooth_elv2', 'speed']
                w.writerow(fields)
                for i, pano in enumerate(filtered_panos):
                    w.writerow([i] + [pano.get(field, '') for field in fields[1:]])
    
    logging.info("Downloading frames")
    if os.path.exists(dir_join('bynum')):
        shutil.rmtree(dir_join('bynum'))
    os.makedirs(dir_join('bynum'))
    if os.path.exists(dir_join('byid')):
        shutil.rmtree(dir_join('byid'))
    os.makedirs(dir_join('byid'))
    
    
    for pano in filtered_panos:
        path = 'pano_img/{}-{}.jpeg'.format(pano['id'], pano['smooth_yaw'])

        if not os.path.exists(path):
            logging.info('{} {} {}'.format(path, pano['if'], pano['i']))
            img = requests.get(
                'http://maps.googleapis.com/maps/api/streetview',
                params={
                    'size': '640x480',
                    'pano': pano['id'],
                    'fov': 110,
                    'heading': pano['smooth_yaw'],
                    'sensor': 'false',
                    'key': 'AIzaSyC74vPZz2tYpRuRWY7kZ8iaQ17Xam1-_-A',
                },
                stream=True,
            )
            img.raise_for_status()

            with DelayedKeyboardInterrupt():
                with open(path, 'wb') as f:
                    shutil.copyfileobj(img.raw, f)
            del img
        os.link(path, dir_join('bynum/{:05d}.jpeg'.format(pano['if'])))
        os.link(path, dir_join('byid/{:05d}-{}-{:06d}.jpeg'.format(pano['i'], pano['id'], pano['if'])))
    
    
    base_speed = 100000/(10*60)  # m/s
    video_items = []
    prev_add_points = []
    i = 0
    #import pudb; pudb.set_trace()
    
    while i < len(panos_with_missing):
        pano = panos_with_missing[i]
        add_points = []
        while i < len(panos_with_missing) - 1 and 'id' not in panos_with_missing[i + 1]:
            i += 1
            add_points.append(panos_with_missing[i])
        all_points = prev_add_points[round(len(prev_add_points) / 2):] + [pano] + add_points[:round(len(add_points) / 2)]
        time = sum((point['dist']/(base_speed * point['speed']) for point in all_points))
        video_items.append((dir_join('bynum/{:05d}.jpeg'.format(pano['if'])), time))
        i += 1
        prev_add_points = add_points
    #video_items = [(dir_join('bynum/{:05d}.jpeg'.format(i)), pano['dist']/(base_speed * pano['speed']))
    #               for i, pano in enumerate(filtered_panos)]
    #video.video(video_items)
    logging.info('Saving video_items.json')
    with open(dir_join('video_items.json'), 'w') as f:
        json_dump_list(video_items, f)
    
    
    video_positions = []
    video_time = 0
    dist = 0
    for i, point in enumerate(panos_with_missing):
        if i % 10 == 0 :
            video_positions.append((round(video_time, 2), dist, point['lat'], point['lng'], point['smooth_elv2']))
        video_time += point['dist']/(base_speed * point['speed'])
        dist += point['dist']
    logging.info('Saving video_positions.json')
    with open(dir_join('video_positions.json'), 'w') as f:
        json_dump_list(video_positions, f)
    
    web_info = dict(
        title =  source['title'],
        bounds = route['routes'][0]['bounds'],
        max_elv = max(panos_with_missing, key=lambda point: point['smooth_elv2'])['smooth_elv2'] + 10,
        #min_elv = max(panos_with_missing, key=lambda point: point['smooth_elv2'])['smooth_elv2'] - 10,
        min_elv = 0,
        route_points = points,
        video_points = video_positions,
    )
    logging.info('Saving web_info.json')
    with open(dir_join('web_info.json'), 'w') as f:
        json.dump(web_info, f)
    
except KeyboardInterrupt:
    logging.error('KeyboardInterrupt')
except:
    logging.exception('')



