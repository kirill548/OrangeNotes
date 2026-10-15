import 'dart:convert';

class DeltaError implements Exception {
  final String message;
  DeltaError(this.message);
  @override
  String toString() => 'DeltaError: $message';
}

String validUuid(Object? value) {
  if (value is! String ||
      !RegExp(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
      ).hasMatch(value)) {
    throw DeltaError('Expected canonical UUIDv4');
  }
  return value;
}

int _utf8Length(String value) {
  final units = value.codeUnits;
  for (var i = 0; i < units.length; i++) {
    final c = units[i];
    if (c >= 0xd800 && c <= 0xdbff) {
      if (++i >= units.length || units[i] < 0xdc00 || units[i] > 0xdfff) {
        throw DeltaError('Unpaired Unicode surrogate');
      }
    } else if (c >= 0xdc00 && c <= 0xdfff) {
      throw DeltaError('Unpaired Unicode surrogate');
    }
  }
  return utf8.encode(value).length;
}

String canonicalJson(Object? value) {
  Object? sorted(Object? v) {
    if (v is Map) {
      final keys = v.keys.cast<String>().toList()..sort();
      return {for (final k in keys) k: sorted(v[k])};
    }
    if (v is List) return v.map(sorted).toList();
    return v;
  }

  return jsonEncode(sorted(value));
}

// jsonDecode discards duplicate object keys. Reject them before decoding using
// a structural scanner which also handles escaped keys and nested objects.
Object? strictJson(String wire) {
  var nesting = 0, quoted = false, escaped = false;
  for (final unit in wire.codeUnits) {
    if (quoted) {
      if (escaped) {
        escaped = false;
      } else if (unit == 92) {
        escaped = true;
      } else if (unit == 34) {
        quoted = false;
      }
    } else if (unit == 34) {
      quoted = true;
    } else if (unit == 123 || unit == 91) {
      if (++nesting > 32) throw DeltaError('JSON nesting limit');
    } else if (unit == 125 || unit == 93) {
      nesting--;
    }
  }
  var i = 0;
  void space() {
    while (i < wire.length && ' \r\n\t'.contains(wire[i])) {
      i++;
    }
  }

  String string() {
    final start = i++;
    while (i < wire.length) {
      if (wire[i] == '\\') {
        i += 2;
        continue;
      }
      if (wire[i++] == '"') {
        return jsonDecode(wire.substring(start, i)) as String;
      }
    }
    throw DeltaError('Unterminated JSON string');
  }

  late void Function() value;
  value = () {
    space();
    if (i >= wire.length) throw DeltaError('Truncated JSON');
    if (wire[i] == '{') {
      i++;
      space();
      final keys = <String>{};
      if (i < wire.length && wire[i] == '}') {
        i++;
        return;
      }
      while (true) {
        space();
        if (i >= wire.length || wire[i] != '"') {
          throw DeltaError('Invalid object');
        }
        if (!keys.add(string())) throw DeltaError('Duplicate JSON field');
        space();
        if (i >= wire.length || wire[i++] != ':') {
          throw DeltaError('Invalid object');
        }
        value();
        space();
        if (i >= wire.length) throw DeltaError('Truncated object');
        final c = wire[i++];
        if (c == '}') return;
        if (c != ',') throw DeltaError('Invalid object');
      }
    } else if (wire[i] == '[') {
      i++;
      space();
      if (i < wire.length && wire[i] == ']') {
        i++;
        return;
      }
      while (true) {
        value();
        space();
        if (i >= wire.length) throw DeltaError('Truncated array');
        final c = wire[i++];
        if (c == ']') return;
        if (c != ',') throw DeltaError('Invalid array');
      }
    } else if (wire[i] == '"') {
      string();
    } else {
      final start = i;
      while (i < wire.length && !',]} \r\n\t'.contains(wire[i])) {
        i++;
      }
      if (i == start) throw DeltaError('Invalid token');
    }
  };
  try {
    value();
    space();
    if (i != wire.length) throw DeltaError('Trailing JSON');
    return jsonDecode(wire);
  } on FormatException {
    throw DeltaError('Invalid JSON');
  }
}

class _Register {
  final int clock;
  final String actor, value;
  _Register(this.clock, this.actor, this.value);
  int compareTo(_Register b) {
    final n = clock.compareTo(b.clock);
    return n == 0 ? actor.compareTo(b.actor) : n;
  }
}

class Replica {
  static const maxBytes = 262144,
      maxClock = 9007199254740991,
      maxDots = 8192,
      maxEntities = 4096,
      maxHistoryBytes = 8388608;
  final String workspaceId, actorId;
  int clock = 0, _historyBytes = 0;
  final _dots = <String, String>{};
  final _entities = <String, Map<String, _Register>>{};
  final _bodies = <String, List<_Register>>{};
  Replica(String workspace, String actor)
    : workspaceId = validUuid(workspace),
      actorId = validUuid(actor);
  String makeDelta(String entity, Map<String, String> fields) {
    final wire = canonicalJson({
      'version': 1,
      'workspace_id': workspaceId,
      'actor_id': actorId,
      'clock': clock + 1,
      'ops': [
        {'entity_id': entity, 'fields': fields},
      ],
    });
    applyDelta(wire);
    return wire;
  }

  bool applyDelta(String wire) {
    if (_utf8Length(wire) > maxBytes) {
      throw DeltaError('Delta exceeds byte limit');
    }
    final data = strictJson(wire);
    bool keys(Object? v, Set<String> expected) =>
        v is Map &&
        v.length == expected.length &&
        v.keys.every(expected.contains);
    if (!keys(data, {'version', 'workspace_id', 'actor_id', 'clock', 'ops'})) {
      throw DeltaError('Invalid envelope');
    }
    final d = data as Map;
    if (d['version'] is! int ||
        d['version'] != 1 ||
        validUuid(d['workspace_id']) != workspaceId) {
      throw DeltaError('Version or workspace mismatch');
    }
    final actor = validUuid(d['actor_id']), c = d['clock'];
    if (c is! int || c < 1 || c > maxClock) {
      throw DeltaError('Invalid Lamport clock');
    }
    final ops = d['ops'];
    if (ops is! List || ops.isEmpty || ops.length > 32) {
      throw DeltaError('Invalid operation count');
    }
    final seen = <String>{};
    const limits = {'title': 1024, 'body': 131072, 'lifecycle': 7};
    for (final op in ops) {
      if (!keys(op, {'entity_id', 'fields'})) {
        throw DeltaError('Invalid operation');
      }
      final entity = validUuid(op['entity_id']);
      if (!seen.add(entity)) throw DeltaError('Repeated entity');
      final fields = op['fields'];
      if (fields is! Map ||
          fields.isEmpty ||
          fields.keys.any((k) => !limits.containsKey(k))) {
        throw DeltaError('Invalid fields');
      }
      for (final e in fields.entries) {
        if (e.value is! String ||
            _utf8Length(e.value as String) > limits[e.key]!) {
          throw DeltaError('Invalid field value');
        }
        if (e.key == 'lifecycle' &&
            e.value != 'active' &&
            e.value != 'deleted') {
          throw DeltaError('Invalid lifecycle');
        }
      }
    }
    final canonical = canonicalJson(d), dot = '$actor:$c';
    if (_dots.containsKey(dot)) {
      if (_dots[dot] != canonical) throw DeltaError('Actor clock equivocation');
      return false;
    }
    final size = utf8.encode(canonical).length;
    if (_dots.length >= maxDots ||
        {..._entities.keys, ...seen}.length > maxEntities ||
        _historyBytes + size > maxHistoryBytes) {
      throw DeltaError('Replica capacity reached; snapshot required');
    }
    for (final op in ops) {
      final entity = op['entity_id'] as String,
          registers = _entities.putIfAbsent(entity, () => {});
      for (final entry in (op['fields'] as Map).entries) {
        final r = _Register(c, actor, entry.value as String),
            key = entry.key as String;
        if (key == 'body') {
          final versions = _bodies.putIfAbsent(entity, () => []);
          versions.add(r);
          versions.sort((a, b) => b.compareTo(a));
          if (versions.length > 16) versions.removeLast();
        }
        if (registers[key] == null || r.compareTo(registers[key]!) > 0) {
          registers[key] = r;
        }
      }
    }
    _dots[dot] = canonical;
    _historyBytes += size;
    if (c > clock) clock = c;
    return true;
  }

  Map<String, String>? get(String entity) {
    validUuid(entity);
    final registers = _entities[entity];
    if (registers == null) return null;
    return {
      for (final e in registers.entries) e.key: e.value.value,
      'lifecycle': registers['lifecycle']?.value ?? 'active',
    };
  }

  List<Map<String, Object>> bodyConflicts(String entity) {
    validUuid(entity);
    return (_bodies[entity] ?? [])
        .skip(1)
        .map(
          (r) => <String, Object>{
            'clock': r.clock,
            'actor_id': r.actor,
            'body': r.value,
          },
        )
        .toList();
  }

  Iterable<String> get entityIds => _entities.keys;
}
