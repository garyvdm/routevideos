#!/usr/bin/env python3

import logging

import gi.repository.GObject as GObject
import gi.repository.Gst as Gst

logging.basicConfig(level=logging.DEBUG)


GObject.threads_init()
Gst.init(None)

def video(items):
    mainloop = GObject.MainLoop()
    
    pipeline = Gst.parse_launch(
        'appsrc name=src block=true caps="image/jpeg" ! '
        'jpegdec ! '
        #'progressreport update-freq=1 ! '
        #'timeoverlay text=\"Stream time:\" shaded-background=true ! '
        #'ffmpegcolorspace ! '
        #'vp8enc threads=2 ! webmmux ! filesink location=test.webm'
        #'x264enc quantizer=50 ! matroskamux ! filesink location=test.mkv'
        #'jpegenc ! matroskamux ! filesink name=sink'
        'autovideosink'
    )
    
    current_i = 0
    total_time = 0
    
    def src_need_data(src, need_bytes):
        nonlocal current_i, total_time
        
        filename, duration = items[current_i]
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
        if len(items) - 1 <= current_i :
            logging.info('Done')
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
