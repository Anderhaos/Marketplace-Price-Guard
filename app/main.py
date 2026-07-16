"""Точка входа-подсказка.

Основной интерфейс проекта запускается через app/web_server.py.
Этот файл оставлен только чтобы случайный запуск app/main.py не менял даже тестовые данные.
"""


def main():
    print("Marketplace Price Guard запускается командой:")
    print("python app/web_server.py 8001")
    print("Затем открой http://127.0.0.1:8001")


if __name__ == "__main__":
    main()
