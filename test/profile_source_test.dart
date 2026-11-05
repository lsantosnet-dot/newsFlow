import 'package:flutter_test/flutter_test.dart';
import 'package:newsflow/models/profile.dart';

void main() {
  group('ProfileSource', () {
    test('preserva campos desconhecidos (ex: fonte github) ao regravar', () {
      final map = {
        'type': 'github',
        'name': 'GitHub: Kubernetes',
        'queries': ['topic:kubernetes', 'topic:docker'],
        'days': 14,
        'min_stars': 30,
        'limit': 8,
      };

      final source = ProfileSource.fromMap(map);
      final saved = source.toMap();

      expect(saved['queries'], ['topic:kubernetes', 'topic:docker']);
      expect(saved['days'], 14);
      expect(saved['min_stars'], 30);
      expect(saved['limit'], 8);
      expect(saved['type'], 'github');
    });

    test('subtítulo da fonte github lista as buscas, a janela e as estrelas', () {
      final source = ProfileSource.fromMap({
        'type': 'github',
        'name': 'x',
        'queries': ['language:swift', ''],
        'days': 14,
        'min_stars': 30,
      });

      expect(source.isGithub, isTrue);
      expect(source.subtitle, 'language:swift · últimos 14 dias · ≥ 30 estrelas');
    });

    test('campos conhecidos continuam funcionando e não vazam para o extra', () {
      final source = ProfileSource.fromMap({
        'type': 'reddit',
        'name': 'r/x',
        'subreddit': 'x',
        'min_score': 10,
      });

      expect(source.extra, isEmpty);
      expect(source.toMap()['subreddit'], 'x');
    });
  });
}
