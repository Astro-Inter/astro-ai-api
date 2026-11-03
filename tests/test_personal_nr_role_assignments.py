"""Execute the assignment SQL with fixtures where role and unit links differ."""
from contextlib import contextmanager
import sqlite3

import pytest

from app.core import config
from app.modules.sst import tools


@pytest.mark.parametrize('unit_links', [[], [1], [1, 5, 32]])
def test_role_assignments_survive_missing_or_partial_unit_links(monkeypatch, unit_links):
    db = sqlite3.connect(':memory:')
    db.executescript('''
        CREATE TABLE usuario (id_usuario INTEGER, nome TEXT, cargo_id INTEGER, unidade_id INTEGER, firebase_uid TEXT);
        CREATE TABLE cargo (id_cargo INTEGER, nome TEXT);
        CREATE TABLE unidade (id_unidade INTEGER, nome TEXT);
        CREATE TABLE cargo_nr (cargo_id INTEGER, nr_id INTEGER);
        CREATE TABLE unidade_nr (unidade_id INTEGER, nr_id INTEGER);
        CREATE TABLE nr_catalogo (codigo_nr INTEGER, titulo TEXT, tempo_reciclagem_mes INTEGER, revogada BOOLEAN);
        INSERT INTO usuario VALUES (1,'Pessoa','1',1,'current-user'), (2,'Outra pessoa',2,1,'other-user');
        INSERT INTO cargo VALUES (1,'Cargo A'),(2,'Cargo B');
        INSERT INTO unidade VALUES (1,'Unidade A');
        INSERT INTO cargo_nr VALUES (1,1),(1,5),(1,32),(1,2),(2,99);
        INSERT INTO nr_catalogo VALUES (1,'NR-1',NULL,FALSE),(5,'NR-5',NULL,FALSE),
            (32,'NR-32',NULL,FALSE),(2,'NR-2',NULL,TRUE),(99,'Outra',NULL,FALSE);
    ''')
    db.executemany('INSERT INTO unidade_nr VALUES (1,?)', [(nr,) for nr in unit_links])

    class Connection:
        @contextmanager
        def cursor(self):
            class Cursor:
                def execute(self, sql, parameters):
                    self.rows = db.execute(sql.replace('%s', '?'), parameters)
                def fetchall(self):
                    return self.rows.fetchall()
            yield Cursor()

    @contextmanager
    def connection():
        yield Connection()

    monkeypatch.setattr(config, 'DATABASE_URL', 'configured-for-test')
    monkeypatch.setattr(tools, 'get_postgres_connection', connection)
    try:
        result = tools.consultar_nrs_obrigatorias.invoke({}, config={'configurable': {
            'usuario_atual': {'uid': 'current-user', 'role': 'COLABORADOR'}}})
        assert result['status'] == 'ok'
        assert [nr['numero'] for nr in result['nrs']] == [1, 5, 32]
        assert result['quantidade'] == 3
        assert 'unidade_nr' not in result['fonte']['tabelas']
        db.execute('DELETE FROM cargo_nr WHERE cargo_id=1')
        empty = tools.consultar_nrs_obrigatorias.invoke({}, config={'configurable': {
            'usuario_atual': {'uid': 'current-user', 'role': 'COLABORADOR'}}})
        assert empty['nrs'] == [] and empty['quantidade'] == 0
    finally:
        db.close()
