"""Index standard CRI ACB cue sheets and match AWB files by embedded MD5."""

import argparse
import csv
import hashlib
import json
import re
import shutil
import struct
import subprocess
import tempfile
import wave
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def table(data, wanted):
    """Read selected columns of one CRI @UTF table, not unrelated binary payloads."""
    if not data:
        return []
    if data[:4] != b'@UTF':
        raise ValueError('not a CRI UTF table')
    length, _, rows, strings, blobs, _, ncols, width, nrows = struct.unpack_from('>IHHIIIHHI', data, 4)
    rows, strings, blobs, end = rows + 8, strings + 8, blobs + 8, length + 8
    if not (32 <= rows <= rows + width * nrows <= strings <= blobs <= end <= len(data)):
        raise ValueError('invalid UTF table bounds')

    def string(offset):
        start = strings + offset
        return data[start:data.index(b'\0', start, blobs)].decode('utf-8', 'replace')

    fields = []
    schema, row_offset = 32, 0
    for _ in range(ncols):
        flags, name_offset = struct.unpack_from('>BI', data, schema)
        name = string(name_offset)
        schema += 5
        typ = flags & 15
        fmt = ('B', 'b', 'H', 'h', 'I', 'i', 'Q', 'q', 'f', 'd', 'I', 'II', '16s')[typ]
        size = struct.calcsize('>' + fmt)
        fixed = bool(flags & 32)
        if fixed:
            offset, schema = schema, schema + size
        elif flags & 64:
            offset, row_offset = row_offset, row_offset + size
        else:
            continue
        if name in wanted:
            fields.append((name, typ, '>' + fmt, fixed, offset))
    result = []
    for i in range(nrows):
        row = {}
        for name, typ, fmt, fixed, offset in fields:
            values = struct.unpack_from(fmt, data, offset if fixed else rows + i * width + offset)
            if typ == 11:
                start, size = values
                if start + size > end - blobs:
                    raise ValueError('UTF blob is out of bounds')
                row[name] = data[blobs + start:blobs + start + size]
            elif typ == 10:
                row[name] = string(values[0])
            else:
                row[name] = values[0]
        result.append(row)
    return result


def cue_sheet(file):
    wanted = {'Name', 'CueNameTable', 'CueTable', 'StreamAwbHash', 'AwbFile', 'WaveformTable'}
    row, = table(file.read_bytes(), wanted)
    cues = table(row['CueNameTable'], {'CueName', 'CueIndex'})
    ids = table(row['CueTable'], {'CueId'})
    data = row.get('StreamAwbHash', b'')
    digest = table(data, {'Hash'})[0]['Hash'] if data.startswith(b'@UTF') else data
    return {'name': row['Name'], 'hash': digest.hex() if any(digest) else '',
            'embedded': bool(row.get('AwbFile')), 'cues': [(c['CueName'], ids[c['CueIndex']]['CueId']) for c in cues],
            'waves': len(table(row['WaveformTable'], set()))}


def md5(file):
    digest = hashlib.md5()
    for block in iter(lambda: file.read(1024 * 1024), b''):
        digest.update(block)
    return digest.hexdigest()


def catalog(game, out, inputs=None, *, index_dir=None):
    sheets, banks, errors = [], defaultdict(list), []
    for path in (game / 'resources').rglob('*') if inputs is None else inputs:
        if not path.is_file():
            continue
        rel = path.relative_to(game).as_posix()
        with path.open('rb') as f:
            magic = f.read(4)
            if magic == b'AFS2':
                f.seek(0)
                banks[md5(f)].append(rel)
            elif magic == b'@UTF':
                try:
                    sheet = cue_sheet(path)
                    sheets.append(dict(acb=rel, **sheet))
                except (IndexError, KeyError, struct.error, ValueError) as err:
                    errors.append((rel, str(err)))
    for sheet in sheets:
        matches = banks.get(sheet['hash'], [])
        sheet['awb'] = min(matches) if matches else ''
    sheets.sort(key=lambda s: (s['name'], s['acb']))
    index_dir = index_dir or out
    (index_dir / 'sheets.json').write_text(json.dumps(sheets, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    with (index_dir / 'index-errors.csv').open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(('source', 'error'))
        writer.writerows(errors)
    print('sheets', len(sheets), 'awb files', sum(map(len, banks.values())), 'matched sheets', sum(bool(s['awb']) for s in sheets),
          'with embedded audio', sum(s['embedded'] for s in sheets), 'index errors', len(errors))
    return sheets


def safe_name(name):
    return re.sub(r'[^\w.-]', '_', name)[:120].strip('. ') or 'unnamed'


def decode_bank(cli, source, stage, start, end):
    run = subprocess.run([cli, '-i', '-I', '-s', str(start), '-S', str(end), '-o', stage / f'{source.suffix[1:]}-?04s.wav', source],
                         capture_output=True, timeout=180)
    if run.returncode:
        raise ValueError(run.stderr.decode(errors='replace')[:300] or 'decoder failed')
    infos = [json.loads(line) for line in run.stdout.splitlines() if line.strip()]
    if not infos or len(infos) != (end - start + 1 if end else infos[0]['streamInfo']['total']):
        raise ValueError('incomplete decoder subsong list')
    return [(m, source.suffix[1:]) for m in infos]


def resident_streams(acb):
    row, = table(acb.read_bytes(), {'WaveformTable', 'AwbFile'})
    bank = row['AwbFile']
    if bank[:4] != b'AFS2':
        raise ValueError('invalid resident bank')
    id_width = int.from_bytes(bank[6:8], 'little')
    count = int.from_bytes(bank[8:12], 'little')
    ids = {int.from_bytes(bank[16 + i * id_width:16 + (i + 1) * id_width], 'little'): i + 1 for i in range(count)}
    return sorted({ids[w['MemoryAwbId']] for w in table(row['WaveformTable'], {'Streaming', 'MemoryAwbId'}) if w['Streaming'] == 0})


def export_sheet(sheet, game, out, cli):
    name = sheet['name']
    records = []
    try:
        if sheet['hash'] and not sheet['awb']:
            raise ValueError('missing matching AWB; embedded audio may be a prefetch')
        if not (sheet['awb'] or sheet['embedded']):
            raise ValueError('no waveform bank')
        category = safe_name('_'.join(name.split('_')[:2]))
        group = re.search(r'_(\d{5})', name)
        dest = out / 'wav' / category / (group[1] if group else 'other')
        if sheet.get('_discriminator'):
            dest /= sheet['_discriminator']
        dest.mkdir(exist_ok=True, parents=True)
        with tempfile.TemporaryDirectory(dir=out) as tmp:
            stage = Path(tmp)
            source = stage / 'sound.acb'
            shutil.copyfile(game / sheet['acb'], source)
            if sheet['awb']:
                source = stage / 'sound.awb'
                shutil.copyfile(game / sheet['awb'], source)
            infos = decode_bank(cli, source, stage, 1, 0)
            if sheet['awb'] and sheet['embedded'] and len(infos) < sheet['waves']:
                acb = source.with_suffix('.acb')
                for stream_id in resident_streams(acb):
                    infos.extend(decode_bank(cli, acb, stage, stream_id, stream_id))
            cue_ids = dict(sheet['cues'])
            for meta, bank in infos:
                info = meta['streamInfo']
                index, cue = info['index'], info.get('name', '')
                if '[pre]' in cue:
                    raise ValueError('decoder identified prefetch-only stream')
                wav = stage / f'{bank}-{index:04d}.wav'
                with wave.open(str(wav), 'rb') as stream:
                    frames, rate, nch = stream.getnframes(), stream.getframerate(), stream.getnchannels()
                    size = stream.getsampwidth()
                    if size != 2 or frames <= 0 or (frames, rate, nch) != (meta['playSamples'], meta['sampleRate'], meta['channels']):
                        raise ValueError('WAV parameters disagree with decoder metadata')
                    data = stream.readframes(frames)
                    if len(data) != frames * nch * size:
                        raise ValueError('truncated WAV')
                    values = memoryview(data).cast('h')
                    peak = max(map(abs, values[::max(1, len(values) // 4096)]))
                suffix = '' if len(infos) == 1 and cue == name else f'__{index:03d}_{safe_name(cue)}'
                if bank == 'acb' and sheet['awb']:
                    suffix = f'__resident{index:03d}_{safe_name(cue)}'
                target = dest / f'{safe_name(name)}{suffix}.wav'
                wav.replace(target)
                records.append((name, cue, cue_ids.get(cue, ''), index, frames, rate, nch, peak, target.relative_to(out).as_posix()))
        return records, ''
    except (OSError, EOFError, KeyError, ValueError, wave.Error, subprocess.SubprocessError) as err:
        return records, str(err)


def decode(sheets, args):
    selected = [s for s in sheets if re.search(args.select, s['name'])]
    if not selected:
        raise SystemExit('No cue sheets match the selection')
    if not args.cli.is_file():
        raise SystemExit(f'Missing vgmstream-cli: {args.cli}')
    fields = ('sheet', 'cue', 'cue_id', 'subsong', 'frames', 'rate', 'channels', 'sampled_pcm_peak', 'wav')
    suffix = 'all' if args.select == '' else 'voices' if args.select == '^voice_' else hashlib.sha256(args.select.encode()).hexdigest()[:12]
    index_dir = getattr(args, 'index_dir', args.out)
    manifest = index_dir / f'decoded-{suffix}.csv'
    previous = defaultdict(list)
    if getattr(args, 'reuse', None) and manifest.is_file():
        with manifest.open(encoding='utf-8-sig', newline='') as f:
            for row in csv.DictReader(f):
                rel = Path(row['wav'])
                if not rel.anchor and '..' not in rel.parts:
                    previous[row['sheet']].append([row[k] for k in fields])
    names = defaultdict(int)
    for sheet in selected:
        names[sheet['name']] += 1
    for sheet in selected:
        if names[sheet['name']] > 1:
            sheet['_discriminator'] = hashlib.sha256(sheet['acb'].encode()).hexdigest()[:12]

    def cached_or_export(sheet):
        rows = previous[sheet['name']]
        if (names[sheet['name']] == 1 and sheet['acb'] in getattr(args, 'reuse', ()) and
                len(rows) == sheet['waves'] and all((args.out / r[-1]).is_file() for r in rows)):
            return rows, ''
        return export_sheet(sheet, args.game, args.out, args.cli)

    good = bad = 0
    stage = manifest.with_suffix('.partial.csv')
    with stage.open('w', newline='', encoding='utf-8-sig') as success, \
         (index_dir / f'failed-{suffix}.csv').open('w', newline='', encoding='utf-8-sig') as failure, \
         ThreadPoolExecutor(max_workers=args.jobs) as pool:
        ok, fail = csv.writer(success), csv.writer(failure)
        ok.writerow(fields)
        fail.writerow(('sheet', 'acb', 'error'))
        for i, (s, (rows, error)) in enumerate(zip(selected, pool.map(cached_or_export, selected)), 1):
            ok.writerows(rows)
            good += len(rows)
            if error:
                fail.writerow((s['name'], s['acb'], error))
                bad += 1
            if i % 1000 == 0:
                success.flush()
                failure.flush()
                print(f'{i}/{len(selected)} sheets; {good} WAVs, {bad} failed sheets', flush=True)
    stage.replace(manifest)
    print(f'{len(selected)} sheets; {good} WAVs, {bad} failed sheets', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game', type=Path, required=True, help='NekoConne installation directory')
    parser.add_argument('--out', type=Path, default=Path('exports/audio'))
    parser.add_argument('--cli', type=Path, default=Path(__file__).resolve().parents[1] / '.tools/audio/vgmstream-r2168/vgmstream-cli.exe')
    parser.add_argument('--select', help='Decode cue sheet names matching this regular expression; ^voice_ for all voices')
    parser.add_argument('--jobs', type=int, default=6)
    parser.add_argument('--reindex', action='store_true')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if not 1 <= args.jobs <= 16:
        parser.error('--jobs must be 1..16')
    index = args.out / 'sheets.json'
    sheets = catalog(args.game, args.out) if args.reindex or not index.exists() else json.loads(index.read_text(encoding='utf-8'))
    if args.select:
        decode(sheets, args)
        if args.select == '^voice_':
            if __package__:
                from .export_audio_library import generate
            else:
                from export_audio_library import generate
            generate(args.out)
