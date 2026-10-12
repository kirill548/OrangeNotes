"""Fail-closed model response boundary. Validation never executes a tool.

Workspace identity is deliberately absent: the application owns that capability.
Only the JSON Schema subset declared here is supported, without coercion.
"""
import json
import re
from copy import deepcopy
from datetime import datetime


class ToolValidationError(ValueError):
    """Untrusted model output is unsuitable for business logic."""


_TEXT = {'type': 'string', 'minLength': 1, 'maxLength': 100}
_DATE = {'type': ['string', 'null'], 'pattern': r'^[0-9]{4}-[0-9]{2}-[0-9]{2}$'}
_TIME = {'type': ['string', 'null'], 'pattern': r'^[0-9]{2}:[0-9]{2}$'}
_RECURRENCE = {'type': ['string', 'null'], 'maxLength': 128,
               'pattern': r'^FREQ=(?:DAILY|WEEKLY;BYDAY=(?:MO|TU|WE|TH|FR|SA|SU)(?:,(?:MO|TU|WE|TH|FR|SA|SU))*);BYHOUR=[0-9]{1,2};BYMINUTE=[0-9]{1,2}$'}
_CREATE = {'type': 'object', 'additionalProperties': False,
           'required': ['title', 'date', 'time', 'recurrence', 'timezone'],
           'properties': {'title': _TEXT, 'date': _DATE, 'time': _TIME,
                          'recurrence': _RECURRENCE,
                          'timezone': {'type': 'string', 'minLength': 1, 'maxLength': 128}}}
TOOL_SCHEMAS = {
    'create_task': deepcopy(_CREATE),
    'create_reminder': deepcopy(_CREATE),
    'search_notes': {'type': 'object', 'additionalProperties': False, 'required': ['query'],
                     'properties': {'query': {'type': 'string', 'minLength': 1, 'maxLength': 1000},
                                    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50}}},
}
TOOL_SCHEMAS['create_reminder']['properties']['date']['type'] = 'string'
TOOL_SCHEMAS['create_reminder']['properties']['time']['type'] = 'string'
for _field in ('date', 'time', 'recurrence'):
    TOOL_SCHEMAS['create_task']['properties'][_field] = {'type': 'null'}
TOOL_DEFINITIONS = [{'type': 'function', 'function': {
    'name': name, 'description': ('Search within the current workspace.' if name == 'search_notes'
                                else 'Prepare a draft; saving requires user confirmation.'),
    'parameters': deepcopy(schema)}} for name, schema in TOOL_SCHEMAS.items()]
MODEL_RESPONSE_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['text', 'tool_calls'],
    'properties': {'text': {'type': 'string', 'maxLength': 8000},
                   'tool_calls': {'type': 'array', 'maxItems': 1,
                                  'items': {'type': 'object', 'additionalProperties': False,
                                            'required': ['name', 'arguments'],
                                            'properties': {'name': {'type': 'string', 'enum': list(TOOL_SCHEMAS)},
                                                           'arguments': {'type': 'object'}}}}}}


def _validate(value, schema, path='$'):
    names = schema['type'] if isinstance(schema['type'], list) else [schema['type']]
    types = {'string': str, 'object': dict, 'array': list, 'integer': int, 'null': type(None)}
    if not any(type(value) is types[name] for name in names):
        raise ToolValidationError(f'{path}: неверный тип значения')
    if value is None:
        return
    if 'enum' in schema and value not in schema['enum']:
        raise ToolValidationError(f'{path}: неизвестное значение')
    if type(value) is str:
        try:
            value.encode('utf-8')
        except UnicodeError as error:
            raise ToolValidationError(f'{path}: некорректный Unicode') from error
        if len(value) < schema.get('minLength', 0) or len(value) > schema.get('maxLength', 8000):
            raise ToolValidationError(f'{path}: недопустимая длина текста')
        if schema.get('minLength') and not value.strip():
            raise ToolValidationError(f'{path}: пустой текст')
        if any(ord(c) < 32 and c not in '\n\t' for c in value):
            raise ToolValidationError(f'{path}: управляющие символы')
        if 'pattern' in schema and re.fullmatch(schema['pattern'], value) is None:
            raise ToolValidationError(f'{path}: неверный формат')
    elif type(value) is int:
        if not schema.get('minimum', value) <= value <= schema.get('maximum', value):
            raise ToolValidationError(f'{path}: значение вне диапазона')
    elif type(value) is dict:
        if any(type(key) is not str for key in value):
            raise ToolValidationError(f'{path}: неверный ключ')
        properties = schema.get('properties', {})
        if not set(schema.get('required', ())).issubset(value):
            raise ToolValidationError(f'{path}: отсутствуют обязательные параметры')
        if schema.get('additionalProperties') is False and set(value) - set(properties):
            raise ToolValidationError(f'{path}: лишние параметры')
        for key in value.keys() & properties.keys():
            _validate(value[key], properties[key], path + '.' + key)
    elif type(value) is list:
        if len(value) > schema.get('maxItems', 1):
            raise ToolValidationError(f'{path}: слишком много вызовов')
        for item in value:
            _validate(item, schema['items'], path + '[]')


def validate_tool_call(call):
    """Return a detached validated value, never a mutable input reference."""
    envelope = MODEL_RESPONSE_SCHEMA['properties']['tool_calls']['items']
    _validate(call, envelope)
    name, args = call['name'], call['arguments']
    _validate(args, TOOL_SCHEMAS[name], '$.arguments')
    if name != 'search_notes':
        from app.utils.timezones import zone
        try:
            tz = args['timezone']
            if tz.startswith('UTC') and tz != 'UTC':
                if not re.fullmatch(r'UTC[+-][0-9]{2}:[0-9]{2}', tz) or int(tz[4:6]) > 23 or int(tz[7:9]) > 59:
                    raise ValueError('invalid offset')
            zone(tz)
            if name == 'create_task':
                if any(args[key] is not None for key in ('date', 'time', 'recurrence')):
                    raise ValueError('task must not contain schedule')
            else:
                if args['date'] is None or args['time'] is None:
                    raise ValueError('schedule required')
                stamp = datetime.fromisoformat(args['date'] + 'T' + args['time'])
                if args['recurrence'] is not None:
                    from app.services.assistant_actions import validate_recurrence
                    validate_recurrence(args['recurrence'], stamp.isoformat())
        except (ValueError, TypeError, OverflowError) as error:
            raise ToolValidationError('Некорректная дата, время, повторение или часовой пояс.') from error
    return deepcopy(call)


def validate_model_response(value):
    _validate(value, MODEL_RESPONSE_SCHEMA)
    result = deepcopy(value)
    result['tool_calls'] = [validate_tool_call(call) for call in value['tool_calls']]
    return result


def parse_model_response(raw):
    """Reject malformed/duplicate-key JSON before validating its schema."""
    if type(raw) is not str:
        raise ToolValidationError('Ответ модели превышает допустимый размер.')
    try:
        if len(raw.encode('utf-8')) > 65536:
            raise ToolValidationError('Ответ модели превышает допустимый размер.')
    except UnicodeError as error:
        raise ToolValidationError('Некорректный текст ответа модели.') from error
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ToolValidationError('Повторяющийся ключ JSON.')
            result[key] = value
        return result
    def constant(_):
        raise ToolValidationError('Недопустимое число JSON.')
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
        return validate_model_response(value)
    except (json.JSONDecodeError, RecursionError, UnicodeError) as error:
        raise ToolValidationError('Некорректный JSON-ответ модели.') from error


def validate_ollama_tool_call(call):
    """Translate the native transport envelope; never relax argument validation."""
    if type(call) is not dict or set(call) - {'id', 'function'} or 'function' not in call:
        raise ToolValidationError('Неверная оболочка вызова Ollama')
    function = call['function']
    if type(function) is not dict or set(function) - {'index', 'name', 'arguments'}:
        raise ToolValidationError('Неверная функция Ollama')
    if 'index' in function and (type(function['index']) is not int or function['index'] != 0):
        raise ToolValidationError('Неверный индекс функции')
    return validate_tool_call({'name': function.get('name'), 'arguments': function.get('arguments')})
