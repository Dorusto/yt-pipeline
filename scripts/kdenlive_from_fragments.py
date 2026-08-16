#!/usr/bin/env python3
"""Generate a .kdenlive project timeline from a YAML list of source fragments.

Each fragment is (source video file, in timecode, out timecode). One <chain>
producer is created per unique source file; each fragment becomes a separate
<entry> on a single V1 playlist, in the order given in the YAML.

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
import subprocess
import sys

import yaml


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
    kid = 1

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
            bin_entries.append(f'<entry producer="{chain_id}"/>')
            kid += 1
        chain_id = chain_id_by_file[fname]
        entries.append(f'''<entry in="{frag["in"]}" out="{frag["out"]}" producer="{chain_id}">
   <property name="kdenlive:id">{chain_id_by_file[fname][5:]}</property>
  </entry>''')

    xml = f'''<?xml version='1.0' encoding='utf-8'?>
<mlt LC_NUMERIC="C" producer="main_bin" root="{source_dir}" version="7.40.0">
 <profile colorspace="709" description="HD 1080p 25 fps" display_aspect_den="9" display_aspect_num="16" frame_rate_den="1" frame_rate_num="25" height="1080" progressive="1" sample_aspect_den="1" sample_aspect_num="1" width="1920"/>
 {chr(10).join(chains)}
 <playlist id="main_bin">
  <property name="xml_retain">1</property>
  <property name="kdenlive:docproperties.version">1.1</property>
  <property name="kdenlive:docproperties.kdenliveversion">26.04.3</property>
  <property name="kdenlive:documentnotesversion">2</property>
  {chr(10).join(bin_entries)}
 </playlist>
 <playlist id="playlist0">
  {chr(10).join(entries)}
 </playlist>
 <playlist id="playlist_audio">
  <property name="kdenlive:audio_track">1</property>
  {chr(10).join(entries)}
 </playlist>
 <playlist id="playlist_a2">
  <property name="kdenlive:audio_track">1</property>
 </playlist>
 <playlist id="playlist_v2">
 </playlist>
 <tractor id="tractor0" title="{output.split("/")[-1]}">
  <property name="kdenlive:trackheight">62</property>
  <track producer="playlist_a2" hide="video"/>
  <track producer="playlist_audio" hide="video"/>
  <track producer="playlist0" hide="audio"/>
  <track producer="playlist_v2" hide="audio"/>
 </tractor>
</mlt>
'''
    with open(output, "w") as f:
        f.write(xml)
    print(f"written: {output} ({len(fragments)} fragments, {len(chains)} source clips)")
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
