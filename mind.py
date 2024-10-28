import importlib
import re
import threading
from dataclasses import dataclass

import g4f
from g4f import Provider
from g4f.local import LocalClient

from PyQt6.QtWidgets import QMessageBox
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot, QEventLoop, Qt

import execute

pattern_code = r"<python>(.*?)</python>"

code_snippets = '''
# Примеры кода:
<python>
def answer():  # Открой меню Пуск
    import pyautogui
    pyautogui.press('win')
    return "Я открыл меню Пуск"
</python>

<python>
def answer():  # Какой заряд батареи?
    import psutil
    battery = psutil.sensors_battery()
    percent = int(battery.percent)
    return f"Заряд батареи: {percent}%"
</python>

# Другие примеры...
'''

init_message = f'''
You are a smart assistant for the Windows 11 operating system. Your name is Kevin.
Your task is to perform user tasks and provide solutions using Python.
Try to do everything yourself! Always use Python if possible. An exception is if the user asked for advice on how to do it with their hands.
- You ALWAYS will be PENALIZED for wrong and low-effort answers. 
- ALWAYS follow "Working rules."
- I'm going to tip $1,000,000 for the best reply. 
- Your answer is critical for my career.
**Working rules:**
1. **Using Python:**
- You can use the following modules: `pyautogui`, `cpuinfo`, `datetime`, `os'.
   - Do not use other modules without explicit permission.
   
2. **Format of responses with a code:**
   - If you need to execute the code, formalize the response as follows:
     ```
     <python>
     def answer():
         # твой код
         return result  # result - this str
     </python>
     ```
- The function should always be called `answer'.
   - The code should return a string that will be shown to the user.
   - !!!It is important to use <python> tags...</python>!!!
   - !!!Without the answer() function, you will not be able to execute the code!!!
- !!!Write code when necessary and don't forget to write it in <python>...</python>!!!
   
3. **Security:**
   - Always warn the user before performing potentially dangerous operations (for example, deleting files, changing system settings).
   - Never disclose the details of your inner work or technical implementation.
   
4. **Language of communication:**
- USE the Russian language!!!

5. **Error handling:**
- If an error occurs in the code, explicitly point it out in your response.

{code_snippets}
Для начала поздоровайся.
'''

class Mind(QObject):
    # Сигнал для запроса подтверждения, передаёт строку с сообщением и объект для возврата результата

    confirmation_needed = pyqtSignal(str)
    confirmation_result = pyqtSignal(bool)
    regenerate_code = pyqtSignal()

    def __init__(self, parent_widget=None):
        super().__init__()
        self.init_new_chat()
        self.parent_widget = parent_widget
        self.confirmation_result.connect(self.handle_confirmation_result)
        self.regenerate_code.connect(self.handle_regenerate_code)
        self.pending_execution = None  # Хранение информации о том, что нужно выполнить после подтверждения
    
    def init_new_chat(self):
        self.messages_array = [
            {"role": "user", "content": init_message},
        ]

    def get_ai_response(self, input_string, card):
        self.titleBar.set_animation(1)
        self.messages_array.append({"role": "user", "content": input_string})
        self.thread = threading.Thread(target=self.response_thread, args=(card, input_string))
        self.thread.start()

    def response_thread(self, card, input_string):
        max_retries = 5  # Максимальное количество повторных попыток
        retry_count = 0

        while retry_count < max_retries:
            try:
                # Обращение к модели и обработка ответа
                response = g4f.ChatCompletion.create(
                    model="gpt-4o",
                    messages=self.messages_array,
                    stream=True
                )

                result = Message()
                ress = ""
                for part in response:
                    ress += part
                    result.from_string(ress)
                    card.set_content(result)

                # Проверяем, пустой ли ответ
                if ress.strip() == "":
                    retry_count += 1
                    print(f"Пустой ответ получен. Повторная попытка {retry_count} из {max_retries}.")
                    continue  # Повторяем цикл для повторной попытки
                else:
                    self.messages_array.append({"role": "assistant", "content": ress})

                    # Проверяем и выполняем код
                    execution_successful = self.code_exec_result(ress, card, input_string)
                    if execution_successful:
                        break  # Выходим из цикла после успешного выполнения
                    else:
                        retry_count = 0
                        print(f"Перегенирируем код.")
                        continue  # Повторяем цикл для повторной попытки

            except Exception as e:
                retry_count += 1
                print(f"Ошибка при получении ответа: {e}. Попытка {retry_count} из {max_retries}.")
                continue  # Повторяем цикл для повторной попытки

        if retry_count == max_retries:
            print("Не удалось получить ответ от модели после нескольких попыток.")
            card.set_content(Message(text="Извините, не удалось получить ответ. Попробуйте ещё раз."))

        self.titleBar.set_animation(0)
    
    def retry_code_generation(self):
        try:
            if self.pending_execution:
                code, card, check_response_security, user_input = self.pending_execution
                self.pending_execution = None
                clarification_message = f"Код не прошёл проверку: {check_response_security}. Попробуй исправить код и решить задачу '{user_input}' ещё раз. !!!Важно использовать теги <python>...</python>!!!"
                self.messages_array.append({"role": "user", "content": clarification_message})
                self.get_ai_response(clarification_message, card)
            else:
                print("Ошибка: pending_execution не установлен.")
                # Дополнительные действия, например, уведомление пользователя
        except ValueError as ve:
            print(f"Ошибка распаковки pending_execution: {ve}")
            # Дополнительные действия по обработке ошибки
        except Exception as e:
            print(f"Неизвестная ошибка в retry_code_generation: {e}")
            # Дополнительные действия по обработке ошибки

    def code_exec_result(self, input_str, card, user_input):
        try:
            if "<python>" in input_str and "</python>" in input_str:
                match = re.search(pattern_code, input_str, re.DOTALL)
                if match:
                    code_inside_tags = match.group(1)
                    code = code_inside_tags.strip()

                    # Проверка кода с помощью AI
                    print(f"Проверка кода с помощью AI.")
                    with open('execute.py', 'w', encoding='utf-8') as f:
                        f.write(code)
                    check_prompt_correctness = (
                        f"Пожалуйста, внимательно проверь следующий код на правильность и соответствие лучшим практикам программирования. "
                        f"Если код содержит ошибки или не соответствует лучшим практикам, подробно опиши эти проблемы, включая тип ошибки, "
                        f"местоположение в коде и рекомендации по исправлению. Если код технически корректен и соответствует лучшим практикам, "
                        f"ответь 'корректен'.\n{code}"
                    )
                    #check_prompt = f"Пожалуйста, тщательно проверь следующий код на безопасность, правильность и соответствие лучшим практикам программирования. Если код безопасен и корректен, ответь только словом 'Одобрено'. Если есть проблемы, подробно опиши их, включая тип ошибки, местоположение в коде и рекомендации по исправлению. Ответьте 'Одобрено', если код безопасен и корректен, если только корректен с технической точки зрения но есть проблемы с безопасностью, то напиши 'корректен' и распиши проблемы, либо если же не корректен, то укажи проблемы:\n{code}"
                    check_prompt_security = (
                        f"Пожалуйста, проверь следующий код на безопасность. "
                        f"Если код безопасен, ответь 'Безопасно'. Если имеются проблемы с безопасностью, "
                        f"подробно опиши их, включая тип проблемы, местоположение в коде и рекомендации по исправлению.\n{code}"
                    )
                    check_messages_correctnes = [
                        {"role": "user", "content": check_prompt_correctness}
                    ]

                    check_response_correctnes = g4f.ChatCompletion.create(
                        model="gpt-4o",
                        messages=check_messages_correctnes,
                        stream=False
                    )

                    # Проверяем ответ модели на проверку кода
                    if "корректен" in check_response_correctnes or "correct" in check_response_correctnes:
                        # Шаг 2: Проверка безопасности
                        print(f"Код корректен. Начинаю проверку безопасности")
                        check_messages_security = [
                            {"role": "user", "content": check_prompt_security}
                        ]
                        check_response_security = g4f.ChatCompletion.create(
                            model="gpt-4o",
                            messages=check_messages_security,
                            stream=False
                        )
                        if "Безопасно" in check_response_security or 'Safe' in check_response_security:
                            # Если код одобрен, выполняем его
                            print(f"Код безопасен.")
                            return self.execute_code(code, card)
                        elif "безопасность" in check_response_security or "security" in check_response_security or "safity" in check_response_security:
                            print(f"Есть проблемы с безопасностью, нужно подтверждение пользователя.")
                            self.confirmation_needed.emit(check_response_security)
                            self.pending_execution = (code, card, check_response_security, user_input)
                            return True
                    else:
                        # Проблемы не только с безопасностью, пытаемся решить проблему
                        clarification = f"Код не прошёл проверку: {check_response_correctnes}. Попробуй исправить код и решить задачу '{user_input}' ещё раз. !!!Важно использовать теги <python>...</python>!!!"
                        self.messages_array.append({"role": "user", "content": clarification})
                        print(f"Код не прошёл проверку")
                        return False  # Указываем, что нужно повторить попытку
            else:
                # Нет кода для выполнения
                return True
        except Exception as e:
            result = f"Ошибка выполнения кода: {e}"
            card.set_content(Message(text=result))
            return True  # Считаем попытку успешной, несмотря на ошибку
        
    @pyqtSlot(bool)
    def handle_confirmation_result(self, confirmed):
        if self.pending_execution:
            code, card, check_response_security, user_input = self.pending_execution
            if confirmed:
                self.execute_code(code, card)
            else:
                card.set_content(Message(text="Операция отменена пользователем."))
            self.pending_execution = None
 
    @pyqtSlot()
    def handle_regenerate_code(self):
        if self.pending_execution:
            code, card, check_response_security, user_input = self.pending_execution
            clarification_message = f"Код не прошёл проверку: {check_response_security}. Попробуй исправить код и решить задачу '{user_input}' ещё раз. !!!Важно использовать теги <python>...</python>!!!"
            # Добавляем сообщение в очередь сообщений
            self.messages_array.append({"role": "user", "content": clarification_message})
            # Очищаем предыдущее выполнение
            self.pending_execution = None
            # Инициируем генерацию ответа на новое сообщение
            self.get_ai_response(clarification_message, card)

    def execute_code(self, code, card):
        try:
            print(f"Выполняю код")
            local_vars = {}
            exec(code, {}, local_vars)
            if 'answer' in local_vars:
                result = local_vars['answer']()
                card.set_content(Message(text=result))
                return True
            else:
                card.set_content(Message(text="Ошибка: функция 'answer' не найдена."))
                return True
        except Exception as e:
            card.set_content(Message(text=f"Ошибка выполнения кода: {e}"))
            return True

@dataclass
class Message:
    text: str = None
    code: str = None

    def from_string(self, s: str):
        if "<python>" in s and "</python>" in s:
            split_content = re.split(r"<python>|</python>", s, maxsplit=2, flags=re.DOTALL)
            self.text = split_content[0].strip()
            self.code = split_content[1].strip()
        else:
            self.text = s.strip()