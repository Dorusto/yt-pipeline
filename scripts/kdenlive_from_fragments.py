#!/usr/bin/env python3
"""Generate a .kdenlive project timeline from a YAML list of source fragments.

Each fragment is (source video file, in timecode, out timecode). One <chain>
producer is created per unique source file; each fragment becomes a separate
<entry>, duplicated onto the video track (V1) and the audio track (A1), and
the two are linked as a native Kdenlive AVSplit group so trimming/moving one
moves its audio/video counterpart along with it (same as dragging a clip with
sound onto the timeline by hand, before manually splitting it).

Writes Kdenlive's "Generation 5" project structure (each timeline track is
its own 2-playlist mini-tractor, wrapped in one sequence tractor carrying a
kdenlive:uuid) — matching what Kdenlive 23.04+ itself writes, since the
AVSplit group property only lives at that level.

Usage:
    python3 kdenlive_from_fragments.py fragments.yaml

fragments.yaml format:
    source_dir: /path/to/raw/clips
    output: /path/to/Project.kdenlive
    fragments:
      - file: DJI_..._D.MP4
        in: "00:11:16.320"
        out: "00:11:50.680"
        label: optional-note
      - ...
"""
import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime

import yaml

FPS = 25


def probe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def sec_to_tc(s):
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:06.3f}"


def tc_to_frames(tc, fps=FPS):
    h, m, s = tc.split(":")
    seconds = int(h) * 3600 + int(m) * 60 + float(s)
    return round(seconds * fps)


def build(config_path):
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    source_dir = cfg["source_dir"]
    output = cfg["output"]
    fragments = cfg["fragments"]

    chain_id_by_file = {}
    chains = []
    bin_entries = []
    entries = []
    groups = []
    kid = 1
    cursor_frames = 0

    for frag in fragments:
        fname = frag["file"]
        if fname not in chain_id_by_file:
            dur = probe_duration(f"{source_dir}/{fname}")
            chain_id = f"chain{kid}"
            chain_id_by_file[fname] = chain_id
            out_tc = sec_to_tc(dur)
            chains.append(f'''<chain id="{chain_id}" out="{out_tc}">
  <property name="length">{out_tc}</property>
  <property name="eof">pause</property>
  <property name="resource">{fname}</property>
  <property name="mlt_service">avformat-novalidate</property>
  <property name="seekable">1</property>
  <property name="video_index">0</property>
  <property name="audio_index">1</property>
  <property name="kdenlive:id">{kid}</property>
 </chain>''')
            bin_entries.append(f'<entry in="00:00:00.000" out="{out_tc}" producer="{chain_id}"/>')
            kid += 1
        chain_id = chain_id_by_file[fname]
        entries.append(f'''<entry in="{frag["in"]}" out="{frag["out"]}" producer="{chain_id}">
   <property name="kdenlive:id">{chain_id_by_file[fname][5:]}</property>
  </entry>''')

        dur_frames = tc_to_frames(frag["out"]) - tc_to_frames(frag["in"]) + 1
        groups.append({
            "children": [
                {"data": f"2:{cursor_frames}", "leaf": "clip", "type": "Leaf"},
                {"data": f"1:{cursor_frames}", "leaf": "clip", "type": "Leaf"},
            ],
            "type": "AVSplit",
        })
        cursor_frames += dur_frames

    entries_xml = chr(10).join(entries)
    groups_json = json.dumps(groups, indent=4)
    seq_uuid = "{" + str(uuid.uuid4()) + "}"
    total_tc = sec_to_tc(cursor_frames / FPS)

    xml = f'''<?xml version='1.0' encoding='utf-8'?>
<mlt LC_NUMERIC="C" producer="main_bin" root="{source_dir}" version="7.40.0">
 <profile colorspace="709" description="HD 1080p 25 fps" display_aspect_den="9" display_aspect_num="16" frame_rate_den="1" frame_rate_num="25" height="1080" progressive="1" sample_aspect_den="1" sample_aspect_num="1" width="1920"/>
 {chr(10).join(chains)}
 <producer id="producer0" in="00:00:00.000" out="{total_tc}">
  <property name="length">2147483647</property>
  <property name="eof">continue</property>
  <property name="resource">black</property>
  <property name="aspect_ratio">1</property>
  <property name="mlt_service">color</property>
  <property name="kdenlive:playlistid">black_track</property>
  <property name="mlt_image_format">rgba</property>
  <property name="set.test_audio">0</property>
 </producer>
 <playlist id="playlist0"/>
 <playlist id="playlist1"/>
 <tractor id="tractor0" in="00:00:00.000">
  <property name="kdenlive:audio_track">1</property>
  <property name="kdenlive:trackheight">62</property>
  <property name="kdenlive:timeline_active"/>
  <property name="kdenlive:thumbs_format"/>
  <property name="kdenlive:audio_rec"/>
  <track hide="video" producer="playlist0"/>
  <track hide="video" producer="playlist1"/>
  <filter id="filter0">
   <property name="window">75</property>
   <property name="max_gain">20dB</property>
   <property name="channel_mask">-1</property>
   <property name="mlt_service">volume</property>
   <property name="internal_added">237</property>
   <property name="disable">1</property>
  </filter>
  <filter id="filter1">
   <property name="channel">-1</property>
   <property name="mlt_service">panner</property>
   <property name="internal_added">237</property>
   <property name="start">0.5</property>
   <property name="disable">1</property>
  </filter>
  <filter id="filter2">
   <property name="iec_scale">0</property>
   <property name="mlt_service">audiolevel</property>
   <property name="internal_added">237</property>
   <property name="dbpeak">1</property>
   <property name="disable">1</property>
  </filter>
 </tractor>
 <playlist id="playlist2">
  {entries_xml}
 </playlist>
 <playlist id="playlist3"/>
 <tractor id="tractor1" in="00:00:00.000" out="{total_tc}">
  <property name="kdenlive:audio_track">1</property>
  <property name="kdenlive:trackheight">62</property>
  <property name="kdenlive:timeline_active"/>
  <property name="kdenlive:thumbs_format"/>
  <property name="kdenlive:audio_rec"/>
  <track hide="video" producer="playlist2"/>
  <track hide="video" producer="playlist3"/>
  <filter id="filter3">
   <property name="window">75</property>
   <property name="max_gain">20dB</property>
   <property name="channel_mask">-1</property>
   <property name="mlt_service">volume</property>
   <property name="internal_added">237</property>
   <property name="disable">1</property>
  </filter>
  <filter id="filter4">
   <property name="channel">-1</property>
   <property name="mlt_service">panner</property>
   <property name="internal_added">237</property>
   <property name="start">0.5</property>
   <property name="disable">1</property>
  </filter>
  <filter id="filter5">
   <property name="iec_scale">0</property>
   <property name="mlt_service">audiolevel</property>
   <property name="internal_added">237</property>
   <property name="dbpeak">1</property>
   <property name="disable">1</property>
  </filter>
 </tractor>
 <playlist id="playlist4">
  {entries_xml}
 </playlist>
 <playlist id="playlist5"/>
 <tractor id="tractor2" in="00:00:00.000" out="{total_tc}">
  <property name="kdenlive:trackheight">62</property>
  <property name="kdenlive:timeline_active"/>
  <property name="kdenlive:thumbs_format"/>
  <property name="kdenlive:audio_rec"/>
  <track hide="audio" producer="playlist4"/>
  <track hide="audio" producer="playlist5"/>
 </tractor>
 <playlist id="playlist6"/>
 <playlist id="playlist7"/>
 <tractor id="tractor3" in="00:00:00.000">
  <property name="kdenlive:trackheight">62</property>
  <property name="kdenlive:timeline_active"/>
  <property name="kdenlive:thumbs_format"/>
  <property name="kdenlive:audio_rec"/>
  <track hide="audio" producer="playlist6"/>
  <track hide="audio" producer="playlist7"/>
 </tractor>
 <tractor id="{seq_uuid}" in="00:00:00.000" out="{total_tc}">
  <property name="kdenlive:uuid">{seq_uuid}</property>
  <property name="kdenlive:clipname">Sequence 1</property>
  <property name="kdenlive:sequenceproperties.hasAudio">1</property>
  <property name="kdenlive:sequenceproperties.hasVideo">1</property>
  <property name="kdenlive:sequenceproperties.tracksCount">4</property>
  <property name="kdenlive:sequenceproperties.documentuuid">{seq_uuid}</property>
  <property name="kdenlive:control_uuid">{seq_uuid}</property>
  <property name="kdenlive:producer_type">17</property>
  <property name="kdenlive:id">14</property>
  <property name="kdenlive:clip_type">0</property>
  <property name="kdenlive:file_size">0</property>
  <property name="kdenlive:sequenceproperties.activeTrack">0</property>
  <property name="kdenlive:sequenceproperties.disablepreview">0</property>
  <property name="kdenlive:sequenceproperties.position">1</property>
  <property name="kdenlive:sequenceproperties.scrollPos">0</property>
  <property name="kdenlive:sequenceproperties.zonein">0</property>
  <property name="kdenlive:sequenceproperties.zoneout">0</property>
  <property name="kdenlive:sequenceproperties.zoom">12</property>
  <property name="kdenlive:sequenceproperties.groups">{groups_json}
</property>
  <property name="kdenlive:sequenceproperties.guides">[
]
</property>
  <track producer="producer0"/>
  <track producer="tractor0"/>
  <track producer="tractor1"/>
  <track producer="tractor2"/>
  <track producer="tractor3"/>
 </tractor>
 <playlist id="main_bin">
  <property name="kdenlive:docproperties.audioChannels">2</property>
  <property name="kdenlive:docproperties.kdenliveversion">26.04.3</property>
  <property name="kdenlive:docproperties.profile">atsc_1080p_25</property>
  <property name="kdenlive:docproperties.uuid">{seq_uuid}</property>
  <property name="kdenlive:docproperties.version">1.1</property>
  <property name="kdenlive:documentnotes"/>
  <property name="kdenlive:documentnotesversion">2</property>
  <property name="xml_retain">1</property>
  {chr(10).join(bin_entries)}
  <entry in="00:00:00.000" out="{total_tc}" producer="{seq_uuid}"/>
 </playlist>
 <tractor id="tractor4" in="00:00:00.000" out="{total_tc}">
  <property name="kdenlive:projectTractor">1</property>
  <track in="00:00:00.000" out="{total_tc}" producer="{seq_uuid}"/>
 </tractor>
</mlt>
'''
    if os.path.exists(output):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = f"{output}.bak-{stamp}"
        shutil.copy2(output, backup)
        print(f"backup: {backup}")

    with open(output, "w") as f:
        f.write(xml)
    print(f"written: {output} ({len(fragments)} fragments, {len(chains)} source clips, {len(groups)} AV groups)")
    return output


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    out_path = build(sys.argv[1])
    result = subprocess.run(["melt", "-consumer", "xml", out_path],
                             capture_output=True, text=True)
    if result.returncode != 0:
        print("melt validation FAILED:")
        print(result.stderr[-2000:])
        sys.exit(1)
    print("melt validation OK")
