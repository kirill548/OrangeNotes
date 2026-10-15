class NoteRecord {
  final int id;
  final String uuid, title, body;
  final List<String> tags;
  final bool readOnlyHtml;
  const NoteRecord({
    required this.id,
    required this.uuid,
    required this.title,
    required this.body,
    this.tags = const [],
    this.readOnlyHtml = false,
  });
}
