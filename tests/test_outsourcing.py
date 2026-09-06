"""Regression tests for persisted outsourcing evidence and rule refreshes."""

import json
from unittest.mock import patch

import pytest

from bosshunter.db import (
    add_history,
    get_db,
    insert_job_if_new,
    recompute_outsourcing,
    serialize_job,
)
from bosshunter.outsourcing import (
    _match_company_strict,
    compute_outsourcing_columns,
    load_rules,
    parse_persisted_columns,
)


@pytest.mark.parametrize('company, expected', [
    ('塔塔美食有限公司', []),
    ('法本文化传媒有限公司', []),
    ('上海博朗实业', []),
    ('中软国际有限公司', ['中软国际']),
    (' 中软国际（有限公司） ', ['中软国际']),
    ('中软国际有限责任公司', ['中软国际']),
    ('中软国际股份有限公司', ['中软国际']),
    ('ＮＴＴＤＡＴＡ', ['NTTDATA']),
    ('上海中软国际技术有限公司', []),
])
def test_company_matching_uses_complete_names(company, expected):
    assert _match_company_strict(company, ('塔塔', '法本', '博朗软件', '中软国际', 'NTTDATA')) == expected


@pytest.mark.parametrize('job, expected', [
    ({'company': '塔塔美食有限公司'}, 'clean'),
    ({'company': '法本文化传媒有限公司'}, 'clean'),
    ({'company': '中软国际有限公司', 'jd': 'IT外包 驻场', 'company_industry': '互联网'}, 'confirmed'),
    ({'company': '测试企业', 'jd': '派遣岗位', 'company_industry': '自研产品'}, 'confirmed'),
    ({'company': '测试企业', 'jd': '提供解决方案', 'company_industry': '互联网'}, 'suspected'),
    ({'company': '测试企业', 'title': '短期项目工程师'}, 'suspected'),
    ({'company': '测试企业', 'jd': '项目奖金'}, 'suspected'),
])
def test_signal_precedence_and_boolean_agree(job, expected):
    result = compute_outsourcing_columns(job, load_rules({}))
    assert result['outsourcing_level'] == expected
    assert result['outsourcing_confirmed'] == int(expected == 'confirmed')


def test_user_rules_and_disabled_detection():
    config = {'outsourcing_rules': {'companies_user': ['测试供应商有限公司'], 'keywords_hard_user': ['独立硬词']}}
    for job in ({'company': '测试供应商'}, {'jd': '独立硬词'}):
        assert compute_outsourcing_columns(job, load_rules(config))['outsourcing_level'] == 'confirmed'
    assert compute_outsourcing_columns({'title': '短期工程师'}, load_rules({'outsourcing_rules': {'detect_structural': False}}))['outsourcing_level'] == 'clean'
    config['outsourcing_rules']['enabled'] = False
    result = compute_outsourcing_columns({'company': '中软国际', 'jd': '外包'}, load_rules(config))
    assert result['outsourcing_level'] == 'clean'
    assert result['outsourcing_confirmed'] == 0
    assert result['outsourcing_matches'] is None


def test_bad_user_rule_types_cannot_expand_into_characters_or_crash():
    rules = load_rules({'outsourcing_rules': {
        'companies_user': '测试供应商', 'keywords_hard_user': [None, {}, 1, ' ', '独立硬词'],
        'keywords_soft_user': {'word': 'not a list'}, 'forward_propagate_n': 'not an integer',
        'enabled': 'false',
    }})
    assert not rules.enabled
    assert '测' not in rules.companies
    assert rules.keywords_hard[-1] == '独立硬词'
    assert all(isinstance(word, str) for word in rules.keywords_hard)
    assert rules.forward_propagate_n == 2


@pytest.mark.parametrize('raw', ['1', 'null', '{}', 'true', '"text"', '{broken', 1, None, {'nested': []}])
def test_non_array_persisted_json_is_safe(raw):
    parsed = parse_persisted_columns({'outsourcing_matches': raw, 'outsourcing_layers': raw})
    assert parsed['outsourcing_matches'] == []
    assert parsed['outsourcing_layers'] == []


def test_serialization_is_typed_and_repeatable():
    record = {'outsourcing_level': 'suspected', 'outsourcing_confirmed': 1,
              'outsourcing_matches': json.dumps([{'keyword': '测试词'}, {'keyword': {}}, 1, None]),
              'outsourcing_layers': '["L2", 1, {}]'}
    once = serialize_job(record)
    assert once['outsourcing_confirmed'] is False
    assert once['outsourcing_matches'] == ['测试词']
    assert once['outsourcing_layers'] == ['L2']
    assert serialize_job(dict(once)) == once


def test_new_rows_store_real_timestamps_and_preserve_duplicate_rows(tmp_path):
    conn = get_db(tmp_path / 'jobs.db')
    try:
        job = {'id': 'one', 'title': '测试岗位', 'company': '中软国际有限公司'}
        assert insert_job_if_new(conn, job)
        row = dict(conn.execute('SELECT *, datetime(outsourcing_updated_at) AS parsed_time FROM jobs').fetchone())
        assert row['parsed_time']
        assert row['outsourcing_level'] == 'confirmed'
        assert not insert_job_if_new(conn, {**job, 'company': '别的企业'})
        assert dict(conn.execute('SELECT *, datetime(outsourcing_updated_at) AS parsed_time FROM jobs').fetchone()) == row
    finally:
        conn.close()


def test_first_migration_and_main_error_columns_coexist(tmp_path):
    db_path = tmp_path / 'legacy.db'
    with patch('bosshunter.db._migrate_outsourcing'):
        conn = get_db(db_path)
    conn.execute("INSERT INTO jobs(id,title,company,status) VALUES('old','测试岗位','中软国际有限公司','sent')")
    conn.commit()
    add_history(conn, 'old', 'sent', 'synthetic history')
    conn.close()
    conn = get_db(db_path)
    try:
        columns = {row['name'] for row in conn.execute('PRAGMA table_info(jobs)')}
        assert {'last_error', 'last_error_code', 'outsourcing_level'} <= columns
        row = dict(conn.execute('SELECT * FROM jobs').fetchone())
        assert row['outsourcing_level'] == 'confirmed'
        assert row['outsourcing_confirmed'] == 1
        assert conn.execute('SELECT datetime(outsourcing_updated_at) FROM jobs').fetchone()[0]
        assert row['status'] == 'sent'
        assert conn.execute('SELECT COUNT(*) FROM history').fetchone()[0] == 1
    finally:
        conn.close()
    conn = get_db(db_path)
    try:
        assert dict(conn.execute('SELECT * FROM jobs').fetchone()) == row
    finally:
        conn.close()


def test_rule_refresh_updates_old_and_recycled_rows_without_changing_workflow(tmp_path):
    conn = get_db(tmp_path / 'jobs.db')
    try:
        for job_id in ('active', 'recycled'):
            insert_job_if_new(conn, {'id': job_id, 'title': '测试岗位', 'company': '测试供应商'})
        conn.execute("UPDATE jobs SET deleted_at='2026-01-01',status='skipped' WHERE id='recycled'")
        conn.commit()
        before = [dict(row) for row in conn.execute('SELECT * FROM jobs ORDER BY id')]
        rules = load_rules({'outsourcing_rules': {'companies_user': ['测试供应商']}})
        assert recompute_outsourcing(conn, rules) == 2
        refreshed = [dict(row) for row in conn.execute('SELECT * FROM jobs ORDER BY id')]
        assert all(row['outsourcing_level'] == 'confirmed' for row in refreshed)
        for old, new in zip(before, refreshed):
            assert {k: v for k, v in new.items() if not k.startswith('outsourcing_')} == {k: v for k, v in old.items() if not k.startswith('outsourcing_')}
        assert recompute_outsourcing(conn, rules) == 0
        assert [dict(row) for row in conn.execute('SELECT * FROM jobs ORDER BY id')] == refreshed
        disabled = load_rules({'outsourcing_rules': {'enabled': False}})
        assert recompute_outsourcing(conn, disabled) == 2
        assert all(row[0] == 'clean' for row in conn.execute('SELECT outsourcing_level FROM jobs'))
        conn.execute("UPDATE jobs SET outsourcing_updated_at='CURRENT_TIMESTAMP' WHERE id='active'")
        conn.commit()
        assert recompute_outsourcing(conn, disabled) == 1
        assert all(row[0] for row in conn.execute('SELECT datetime(outsourcing_updated_at) FROM jobs'))
    finally:
        conn.close()


def test_refresh_rolls_back_as_a_batch_when_computation_fails(tmp_path):
    conn = get_db(tmp_path / 'jobs.db')
    try:
        for job_id in ('one', 'two'):
            insert_job_if_new(conn, {'id': job_id, 'company': '测试供应商'})
        rules = load_rules({'outsourcing_rules': {'companies_user': ['测试供应商']}})
        real_compute = compute_outsourcing_columns

        def compute(job, active_rules):
            if job['id'] == 'two':
                raise RuntimeError('synthetic refresh failure')
            return real_compute(job, active_rules)

        with patch('bosshunter.db.compute_outsourcing_columns', side_effect=compute):
            with pytest.raises(RuntimeError, match='synthetic refresh failure'):
                recompute_outsourcing(conn, rules)
        assert all(row[0] == 'clean' for row in conn.execute('SELECT outsourcing_level FROM jobs'))
    finally:
        conn.close()
