import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../data/mobile_store.dart';
import '../domain/note.dart';
import 'note_editor.dart';

class GraphScreen extends StatefulWidget {
  const GraphScreen({super.key, required this.store});
  final MobileStore store;
  @override
  State<GraphScreen> createState() => _GraphScreenState();
}

class _GraphScreenState extends State<GraphScreen> {
  late Future<(List<NoteRecord>, List<Map<String, Object?>>)> _graph;
  final _transform = TransformationController();
  @override
  void initState() {
    super.initState();
    _reload();
  }

  void _reload() {
    _graph = _load();
  }

  Future<(List<NoteRecord>, List<Map<String, Object?>>)> _load() async =>
      (await widget.store.listNotes(), await widget.store.graph());
  @override
  void dispose() {
    _transform.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: const Text('Граф заметок'),
      actions: [
        IconButton(
          tooltip: 'Сбросить масштаб',
          onPressed: () => _transform.value = Matrix4.identity(),
          icon: const Icon(Icons.center_focus_strong),
        ),
      ],
    ),
    body: FutureBuilder<(List<NoteRecord>, List<Map<String, Object?>>)>(
      future: _graph,
      builder: (context, snapshot) {
        if (snapshot.hasError) {
          return const Center(child: Text('Не удалось построить граф.'));
        }
        if (!snapshot.hasData) {
          return const Center(child: CircularProgressIndicator());
        }
        final (notes, edges) = snapshot.data!;
        if (notes.isEmpty) {
          return const Center(
            child: Padding(
              padding: EdgeInsets.all(24),
              child: Text(
                'Создайте заметки и свяжите их через [[Название заметки]]. Здесь появится граф связей.',
              ),
            ),
          );
        }
        // Bound the first view to keep hit targets and painting responsive on phones.
        final shown = notes.take(300).toList();
        final side = math.max(800.0, math.sqrt(shown.length) * 100);
        final positions = _layout(shown, edges, side);
        return Column(
          children: [
            Padding(
              padding: const EdgeInsets.all(12),
              child: Text(
                shown.length < notes.length
                    ? 'Показаны первые ${shown.length} из ${notes.length} заметок. Увеличивайте двумя пальцами; нажмите узел, чтобы открыть.'
                    : 'Увеличивайте двумя пальцами и перемещайте граф. Нажмите узел, чтобы открыть заметку.',
              ),
            ),
            Expanded(
              child: InteractiveViewer(
                transformationController: _transform,
                constrained: false,
                minScale: .2,
                maxScale: 4,
                boundaryMargin: const EdgeInsets.all(200),
                child: SizedBox(
                  width: side,
                  height: side,
                  child: Stack(
                    children: [
                      Positioned.fill(
                        child: CustomPaint(
                          painter: _EdgePainter(
                            positions,
                            edges,
                            Theme.of(context).colorScheme.outlineVariant,
                          ),
                        ),
                      ),
                      for (final note in shown)
                        Positioned(
                          left: positions[note.id]!.dx - 56,
                          top: positions[note.id]!.dy - 28,
                          width: 112,
                          height: 56,
                          child: Semantics(
                            button: true,
                            label: 'Открыть ${note.title}',
                            child: Material(
                              color: Theme.of(context)
                                  .colorScheme
                                  .primaryContainer,
                              borderRadius: BorderRadius.circular(28),
                              child: InkWell(
                                borderRadius: BorderRadius.circular(28),
                                onTap: () async {
                                  await Navigator.push(
                                    context,
                                    MaterialPageRoute<void>(
                                      builder: (_) => NoteEditor(
                                        store: widget.store,
                                        note: note,
                                      ),
                                    ),
                                  );
                                  if (mounted) setState(_reload);
                                },
                                child: Padding(
                                  padding: const EdgeInsets.all(8),
                                  child: Center(
                                    child: Text(
                                      note.title.isEmpty
                                          ? 'Без названия'
                                          : note.title,
                                      maxLines: 2,
                                      overflow: TextOverflow.ellipsis,
                                      textAlign: TextAlign.center,
                                    ),
                                  ),
                                ),
                              ),
                            ),
                          ),
                        ),
                    ],
                  ),
                ),
              ),
            ),
          ],
        );
      },
    ),
  );
}

Map<int, Offset> _layout(
  List<NoteRecord> notes,
  List<Map<String, Object?>> edges,
  double side,
) {
  final result = <int, Offset>{};
  for (var i = 0; i < notes.length; i++) {
    final angle = i * math.pi * (3 - math.sqrt(5));
    final radius = math.sqrt(i / math.max(1, notes.length)) * (side / 2 - 80);
    result[notes[i].id] = Offset(
      side / 2 + math.cos(angle) * radius,
      side / 2 + math.sin(angle) * radius,
    );
  }
  // A bounded spring relaxation avoids blocking the UI on dense graphs.
  for (var step = 0; step < 18; step++) {
    for (final edge in edges.take(3000)) {
      final source = edge['source_id'], target = edge['target_id'];
      final a = result[source], b = result[target];
      if (a == null || b == null || source == target) continue;
      final delta = b - a;
      final distance = delta.distance;
      if (distance < 1) continue;
      final force = delta / distance * ((distance - 150) * .025);
      result[source as int] = Offset(
        (a.dx + force.dx).clamp(64, side - 64),
        (a.dy + force.dy).clamp(40, side - 40),
      );
      result[target as int] = Offset(
        (b.dx - force.dx).clamp(64, side - 64),
        (b.dy - force.dy).clamp(40, side - 40),
      );
    }
  }
  return result;
}

class _EdgePainter extends CustomPainter {
  _EdgePainter(this.positions, this.edges, this.color);
  final Map<int, Offset> positions;
  final List<Map<String, Object?>> edges;
  final Color color;
  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..strokeWidth = 1.5;
    for (final edge in edges.take(3000)) {
      final a = positions[edge['source_id']], b = positions[edge['target_id']];
      if (a != null && b != null) canvas.drawLine(a, b, paint);
    }
  }

  @override
  bool shouldRepaint(covariant _EdgePainter oldDelegate) =>
      oldDelegate.positions != positions ||
      oldDelegate.edges != edges ||
      oldDelegate.color != color;
}
