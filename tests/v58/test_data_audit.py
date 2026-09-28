import gzip
import io
import json
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from research_bot.v58.data_audit import ASSETS, audit_archive, canonical_json, digest, freeze_archives, inspect_csv, write_new


def csv_bytes():
    return b'timestamp,open,high,low,close,volume\n2024-01-01T00:00:00Z,100,102,99,101,10\n2024-01-01T04:00:00Z,101,103,100,102,20\n'


def archive(tmp_path):
    content = io.BytesIO()
    metadata = {"collected_at": "2024-01-01T08:00:00Z", "source": "TEST_FIXTURE", "symbols": {}}
    with zipfile.ZipFile(content, 'w') as z:
        for symbol in sorted(ASSETS):
            filename = symbol.replace('/', '_') + '.csv.gz'
            raw = gzip.compress(csv_bytes(), mtime=0)
            z.writestr(filename, raw)
            metadata['symbols'][symbol] = dict(file=filename, sha256=digest(raw), rows=2,
                first_bar='2024-01-01T00:00:00Z', last_bar='2024-01-01T04:00:00Z')
        z.writestr('manifest.json', json.dumps(metadata))
    path = tmp_path / 'fixture.zip'
    path.write_bytes(content.getvalue())
    return dict(path=path, venue='fixture', expected_sha256=digest(content.getvalue()), source_run=0, source_artifact=0)


def test_archive_member_and_raw_csv_hashes_are_distinct_and_verified(tmp_path):
    spec = archive(tmp_path)
    report, members = audit_archive(**spec)
    assert len(report['datasets']) == 5
    for d in report['datasets']:
        assert d['csv_sha256'] == digest(csv_bytes())
        assert d['file_sha256'] == digest(members[d['file']])
        assert d['partition_eligibility'] == 'DEVELOPMENT_ONLY'
    with pytest.raises(ValueError, match='archive SHA'):
        audit_archive(**dict(spec, expected_sha256='0'*64))


def test_quality_rejects_gaps_duplicates_nonfinite_and_invalid_geometry():
    raw = csv_bytes()
    for invalid in [raw.replace(b'04:00', b'08:00'), raw.replace(b'04:00', b'00:00'),
                    raw.replace(b',102,20', b',nan,20'), raw.replace(b',103,100,', b',99,100,')]:
        with pytest.raises(ValueError):
            inspect_csv(invalid)


def test_freeze_is_replayable_does_not_merge_or_authorize(tmp_path):
    spec = archive(tmp_path)
    out = tmp_path / 'frozen'
    first = freeze_archives([spec], out)
    second = freeze_archives([spec], out)
    assert first == second
    assert not first['event_dataset_authorized']
    assert (out/'raw'/spec['expected_sha256']/'source.zip').read_bytes() == spec['path'].read_bytes()
    with pytest.raises(ValueError, match='duplicate archive'):
        freeze_archives([spec, spec], out)
    with pytest.raises(ValueError, match='immutable'):
        write_new(out/'V58_RECOVERED_DATA_MANIFEST.json', b'changed')
