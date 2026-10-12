"""Explicit, bounded note selection; attached data never changes system rules."""

MAX_ATTACHED_NOTES = 8


def validate_attachment_ids(value):
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or len(value) > MAX_ATTACHED_NOTES:
        raise ValueError('Можно прикрепить не больше 8 заметок.')
    if any(type(note_id) is not int or note_id <= 0 for note_id in value) or len(set(value)) != len(value):
        raise ValueError('Некорректный идентификатор прикреплённой заметки.')
    return list(value)


def merge_sources(attachments, retrieved):
    result = []
    attached_ids = {source.get('note_id', source.get('id')) for source in attachments}
    for source in attachments:
        result.append({**source, 'source_kind': 'attachment'})
    result.extend(source for source in retrieved
                  if source.get('note_id', source.get('id')) not in attached_ids)
    return result
