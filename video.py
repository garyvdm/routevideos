#!/usr/bin/env python3

import logging

import gi.repository.GObject as GObject
import gi.repository.Gst as Gst

logging.basicConfig(level=logging.DEBUG)


GObject.threads_init()
Gst.init(None)

def video(items, location):
    mainloop = GObject.MainLoop()
    
    pipeline = Gst.parse_launch(
        'appsrc name=src block=true caps="image/jpeg,framerate=30/1" ! '
        'jpegdec ! '
        #'progressreport update-freq=1 ! '
        'videoconvert ! '
        #'videorate ! '
        #'timeoverlay text=\"Stream time:\" shaded-background=true ! '
        #'ffmpegcolorspace ! '
        'vp8enc end-usage="vbr" target-bitrate=4096000 ! webmmux ! '
        #'x264enc quantizer=50 ! matroskamux ! '
        #'jpegenc ! matroskamux ! '
        #'autovideosink'
        'filesink name=sink'
    )
    
    pipeline.get_by_name("sink").set_property("location", location)

    current_i = 0
    total_time = 0
    
    def src_need_data(src, need_bytes):
        nonlocal current_i, total_time
        
        filename, duration = items[current_i]
        #logging.debug(filename)
        if os.path.exists(filename):
            with open(filename, 'rb') as f:
                data = f.read()
            #help(Gst.Buffer)
            buf = Gst.Buffer.new_wrapped(data)
            
            duration = duration * Gst.SECOND
            total_time += duration
            
            buf.pts = total_time
            buf.duration = duration
            src.emit("push-buffer", buf)
        
            current_i += 1
            if current_i % 10 == 0:
                logging.info(current_i)
            if len(items) - 1 <= current_i :
                logging.info('Done')
                src.emit("end-of-stream")
        else:
            logging.info('No more files - Done')
            src.emit("end-of-stream")
    
    pipeline.get_by_name("src").connect("need-data", src_need_data)
    
    
    def bus_message(bus, message):
        t = message.type
    
        if t == Gst.MessageType.EOS:
            pipeline.set_state(Gst.State.NULL)
            logging.info('Done')
            mainloop.quit()
    
        if t == Gst.MessageType.ERROR:
            pipeline.set_state(Gst.State.NULL)
            error = message.parse_error()
            logging.error('{}\n{}'.format(*error))
    
    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect("message", bus_message)
    
    pipeline.set_state(Gst.State.PLAYING)
    mainloop.run()


if __name__ == '__main__':
    import logging
    import argparse
    import json
    import functools
    import os
    
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', action='store', help='Route directory. This should contain `video_items.json`.')
    parser.add_argument('--debug', action='store_true', help='Output DEBUG messages.')
    
    args = parser.parse_args()
    dir_join = functools.partial(os.path.join, args.directory)
    
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    logging.getLogger('requests').level = logging.ERROR
    
    logging.info('video_items.json')
    with open(dir_join('video_items.json'), 'r') as f:
        video_items = json.load(f)
    
    video(video_items, dir_join('video.webm'))

