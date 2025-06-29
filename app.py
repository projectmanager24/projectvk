# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify
from flask_cors import CORS
import vk_api
import requests
import os
import re # Для обработки ID групп из ссылок
import time # Для имитации задержки и управления лимитами
import mimetypes # Для определения типа файла

app = Flask(__name__)
CORS(app) # Включаем CORS для разрешения запросов с фронтенда

# Временная папка для загруженных медиафайлов
UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

@app.route('/api/post', methods=['POST'])
def handle_post():
    """
    Обрабатывает запросы на создание поста или предложение новости.
    Принимает Access Token, текст, медиафайлы и список групп.
    """
    token = request.headers.get('Authorization')
    if not token or not token.startswith('Bearer '):
        return jsonify({
            'log': [{'message': 'Ошибка: Access Token не предоставлен или имеет неверный формат.', 'type': 'error'}],
            'successCount': 0,
            'errorCount': 0,
            'publishedLinks': []
        }), 401

    vk_access_token = token.split(' ')[1]

    log_messages = []
    success_count = 0
    error_count = 0
    published_links = []

    try:
        # Инициализация VK API
        # Внимание: здесь мы переинициализируем vk_api для каждого запроса,
        # чтобы гарантировать использование актуального токена.
        vk_session = vk_api.VkApi(token=vk_access_token)
        vk = vk_session.get_api()
        upload = vk_api.VkUpload(vk_session)

        # Получение данных из формы
        post_mode = request.form.get('mode', 'createPost') # 'createPost' или 'suggestNews'
        text_content = request.form.get('textContent', '')
        group_list_raw = request.form.get('groupList', '')
        media_files = request.files.getlist('mediaFiles')

        log_messages.append({'message': f'Начинаем обработку запроса в режиме: {post_mode}', 'type': 'info'})

        groups = [g.strip() for g in group_list_raw.split('\n') if g.strip()]

        if not text_content and not media_files:
            log_messages.append({'message': 'Ошибка: Необходимо ввести текст или выбрать медиафайлы.', 'type': 'error'})
            return jsonify({
                'log': log_messages,
                'successCount': success_count,
                'errorCount': error_count,
                'publishedLinks': published_links
            }), 400

        if not groups:
            log_messages.append({'message': 'Ошибка: Введите список групп.', 'type': 'error'})
            return jsonify({
                'log': log_messages,
                'successCount': success_count,
                'errorCount': error_count,
                'publishedLinks': published_links
            }), 400

        # Обработка медиафайлов: загрузка на сервер VK
        attachments = []
        for media_file in media_files:
            try:
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], media_file.filename)
                media_file.save(filepath)
                log_messages.append({'message': f'Файл "{media_file.filename}" сохранен на сервере.', 'type': 'info'})

                # Определяем тип файла
                mime_type, _ = mimetypes.guess_type(filepath)
                if mime_type and mime_type.startswith('image'):
                    photo = upload.photo_wall(photos=filepath)
                    attachments.append(f'photo{photo[0]["owner_id"]}_{photo[0]["id"]}')
                    log_messages.append({'message': f'Изображение "{media_file.filename}" загружено в VK.', 'type': 'success'})
                elif mime_type and mime_type.startswith('video'):
                    # Это упрощенная имитация для видео. Реальная загрузка видео сложнее:
                    # 1. Сначала нужно вызвать video.save, чтобы получить URL для загрузки.
                    # 2. Затем отправить файл на этот URL.
                    # 3. Затем получить id видео.
                    log_messages.append({'message': f'Внимание: Загрузка видео "{media_file.filename}" не поддерживается в этой демо-версии. Пропускаем.', 'type': 'error'})
                else:
                    log_messages.append({'message': f'Неподдерживаемый тип файла "{media_file.filename}". Пропускаем.', 'type': 'error'})
            except Exception as e:
                log_messages.append({'message': f'Ошибка при загрузке медиафайла "{media_file.filename}": {e}', 'type': 'error'})
            finally:
                if os.path.exists(filepath):
                    os.remove(filepath) # Удаляем временный файл

        # Публикация по группам
        for group_entry in groups:
            group_id = None
            # Извлечение ID группы из ссылки или напрямую
            match_link = re.match(r'(?:https?://)?(?:vk\.com/)?(?:public|club|event)(\d+)', group_entry)
            match_id_negative = re.match(r'^-(\d+)$', group_entry)
            match_id_positive = re.match(r'^(\d+)$', group_entry)

            if match_link:
                group_id = int(match_link.group(1)) * -1
            elif match_id_negative:
                group_id = int(match_id_negative.group(1)) * -1
            elif match_id_positive:
                 # Если это положительный ID, но не начинается с '-', то это может быть ID сообщества,
                 # но для постинга на стену нужен отрицательный ID.
                 # Можно попробовать получить информацию о группе, чтобы убедиться, что это группа.
                 # Для простоты, здесь мы просто используем отрицательный ID.
                group_id = int(match_id_positive.group(1)) * -1
            else:
                log_messages.append({'message': f'Некорректный формат группы: {group_entry}. Пропускаем.', 'type': 'error'})
                error_count += 1
                continue

            log_messages.append({'message': f'Попытка публикации в группу ID: {group_id}...', 'type': 'info'})

            try:
                # VK API wall.post:
                # owner_id: ID пользователя или сообщества, на стену которого должен быть опубликован пост.
                #           Для сообществ необходимо указать отрицательное значение их ID (например, -12345).
                # message: Текст поста.
                # attachments: Список прикреплений.
                # from_group: 1 — пост будет опубликован от имени группы (требует прав администратора группы).
                #             0 (по умолчанию) — пост будет опубликован от имени пользователя.
                #             Для "предложенных новостей" используется 0, а VK сам определяет,
                #             будет ли пост опубликован напрямую или отправлен в предложку,
                #             в зависимости от настроек стены группы.

                # Для обоих режимов ('createPost' и 'suggestNews') мы используем wall.post
                # от имени пользователя (from_group=0). VK сам решает, станет ли это предложкой
                # или прямым постом, в зависимости от настроек группы.
                post_params = {
                    'owner_id': group_id,
                    'message': text_content,
                    'attachments': ','.join(attachments) if attachments else None,
                    'from_group': 0 # Публикация от имени пользователя
                }

                response = vk.wall.post(**post_params)
                post_id = response['post_id']
                
                # Генерация ссылки на пост
                post_link = f'https://vk.com/wall{group_id}_{post_id}'
                
                success_count += 1
                log_messages.append({'message': f'Успешно опубликовано в {group_entry}. ID поста: {post_id}. Ссылка: {post_link}', 'type': 'success'})
                
                # Добавляем ссылку в список опубликованных, если это режим "Создать пост"
                if post_mode == 'createPost':
                    published_links.append(post_link)

            except vk_api.exceptions.ApiError as e:
                error_count += 1
                error_message = f'Ошибка VK API для группы {group_entry}: {e.code} - {e.error}'
                if e.code == 100: # Invalid parameter
                    error_message += ' (Возможно, некорректный ID группы или права)'
                elif e.code == 15: # Access denied: group is private
                    error_message += ' (Нет доступа к группе)'
                elif e.code == 214: # Access to posting denied
                    error_message += ' (Публикация на стену запрещена, возможно только через предложку или недостаточно прав)'
                log_messages.append({'message': error_message, 'type': 'error'})
            except Exception as e:
                error_count += 1
                log_messages.append({'message': f'Неизвестная ошибка при публикации в группу {group_entry}: {e}', 'type': 'error'})
            
            # Небольшая задержка между запросами для предотвращения превышения лимитов API
            time.sleep(1) 
            
    except vk_api.exceptions.AuthError as e:
        log_messages.append({'message': f'Ошибка аутентификации VK API: {e}. Проверьте ваш Access Token.', 'type': 'error'})
        error_count += 1
    except requests.exceptions.ConnectionError:
        log_messages.append({'message': 'Ошибка сети: Не удалось подключиться к серверам VK.', 'type': 'error'})
        error_count += 1
    except Exception as e:
        log_messages.append({'message': f'Критическая ошибка бэкенда: {e}', 'type': 'error'})
        error_count += 1
    
    return jsonify({
        'log': log_messages,
        'successCount': success_count,
        'errorCount': error_count,
        'publishedLinks': published_links
    })

if __name__ == '__main__':
    app.run(debug=True, port=5000)

