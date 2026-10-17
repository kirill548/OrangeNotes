import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

class TrustedDevice {
  const TrustedDevice(this.actorId, this.name, this.fingerprint);
  final String actorId;
  final String name;
  final String fingerprint;
}

abstract class PairingController extends ChangeNotifier {
  String get status;
  List<TrustedDevice> get devices;
  Future<void> pairQr(String raw, {required bool confirmed});
  Future<void> revoke(String actorId);
  Future<void> syncNow(String actorId);
  Future<void> importIdentity() async {}
}

class PairingScreen extends StatelessWidget {
  const PairingScreen({super.key, required this.controller});
  final PairingController controller;

  Future<void> _scan(BuildContext context) async {
    final raw = await Navigator.of(context)
        .push<String>(MaterialPageRoute(builder: (_) => const _QrScanner()));
    if (raw == null || !context.mounted) return;
    try {
      final parsed = jsonDecode(raw);
      if (parsed is! Map<String, dynamic>) throw const FormatException();
      final fingerprint = parsed['fingerprint_sha256'] ?? parsed['fingerprint'];
      if (fingerprint is! String ||
          !RegExp(r'^[a-fA-F0-9]{64}$').hasMatch(fingerprint)) {
        throw const FormatException('В QR-коде нет полного SHA256-отпечатка.');
      }
      final confirmed = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('Проверка устройства'),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'Сравните полный отпечаток с экраном другого устройства. Подтверждайте только знакомое устройство.',
                ),
                const SizedBox(height: 16),
                SelectableText(fingerprint),
              ],
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Отмена'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Отпечатки совпадают'),
            ),
          ],
        ),
      );
      if (confirmed != true) return;
      await controller.pairQr(raw, confirmed: true);
    } catch (error) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Не удалось подключить устройство: $error')),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Синхронизация')),
    body: SafeArea(
      child: AnimatedBuilder(
        animation: controller,
        builder: (context, _) => ListView(
          padding: const EdgeInsets.all(20),
          children: [
            Text(
              controller.status,
              semanticsLabel: 'Статус синхронизации: ${controller.status}',
            ),
            const SizedBox(height: 16),
            const Text(
              'Заметки доступны без сети. Для обмена подключите доверенное устройство в той же локальной сети.',
            ),
            const SizedBox(height: 16),
            OutlinedButton.icon(
              onPressed: () async {
                try {
                  await controller.importIdentity();
                } catch (e) {
                  if (context.mounted) {
                    ScaffoldMessenger.of(context).showSnackBar(
                      SnackBar(
                        content: Text('Не удалось подключить сертификат: $e'),
                      ),
                    );
                  }
                }
              },
              icon: const Icon(Icons.verified_user_outlined),
              label: const Text('Подключить сертификат устройства'),
            ),
            const SizedBox(height: 8),
            FilledButton.icon(
              onPressed: () => _scan(context),
              icon: const Icon(Icons.qr_code_scanner),
              label: const Text('Сканировать QR-код'),
            ),
            const SizedBox(height: 24),
            const Text(
              'Доверенные устройства',
              style: TextStyle(fontWeight: FontWeight.bold),
            ),
            if (controller.devices.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 16),
                child: Text('Устройства ещё не подключены.'),
              ),
            for (final device in controller.devices)
              ListTile(
                title: Text(device.name),
                subtitle: Text(
                  device.fingerprint,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
                onTap: () async {
                  try {
                    await controller.syncNow(device.actorId);
                  } catch (e) {
                    if (context.mounted) {
                      ScaffoldMessenger.of(context).showSnackBar(
                        SnackBar(content: Text('Обмен не завершён: $e')),
                      );
                    }
                  }
                },
                trailing: IconButton(
                  tooltip: 'Отключить устройство',
                  icon: const Icon(Icons.link_off),
                  onPressed: () async {
                    final yes = await showDialog<bool>(
                      context: context,
                      builder: (context) => AlertDialog(
                        title: const Text('Отключить устройство?'),
                        content: const Text(
                          'Новые изменения больше не будут передаваться этому устройству. Уже полученные копии заметок останутся на нём.',
                        ),
                        actions: [
                          TextButton(
                            onPressed: () => Navigator.pop(context, false),
                            child: const Text('Отмена'),
                          ),
                          FilledButton(
                            onPressed: () => Navigator.pop(context, true),
                            child: const Text('Отключить'),
                          ),
                        ],
                      ),
                    );
                    if (yes == true) {
                      try {
                        await controller.revoke(device.actorId);
                      } catch (e) {
                        if (context.mounted) {
                          ScaffoldMessenger.of(context).showSnackBar(
                            SnackBar(content: Text('Не удалось отключить: $e')),
                          );
                        }
                      }
                    }
                  },
                ),
              ),
          ],
        ),
      ),
    ),
  );
}

class _QrScanner extends StatefulWidget {
  const _QrScanner();
  @override
  State<_QrScanner> createState() => _QrScannerState();
}

class _QrScannerState extends State<_QrScanner> {
  final _camera = MobileScannerController();
  bool _received = false;
  @override
  void dispose() {
    _camera.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('QR-код другого устройства')),
    body: MobileScanner(
      controller: _camera,
      errorBuilder: (context, error) => const Center(
        child: Padding(
          padding: EdgeInsets.all(24),
          child: Text(
            'Камера недоступна. Разрешите доступ к камере в настройках телефона.',
          ),
        ),
      ),
      onDetect: (capture) {
        for (final barcode in capture.barcodes) {
          final raw = barcode.rawValue;
          if (!_received && raw != null) {
            _received = true;
            Navigator.pop(context, raw);
            break;
          }
        }
      },
    ),
  );
}
