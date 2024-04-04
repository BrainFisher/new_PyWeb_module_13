from fastapi import FastAPI, Request, File, UploadFile, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from functools import wraps
from datetime import datetime, timedelta
from pydantic import BaseModel
from uuid import uuid4
from email.utils import parseaddr
import smtplib
import cloudinary.uploader
import os
from dotenv import load_dotenv

# Завантаження змінних середовища з файлу .env
load_dotenv()

# Словник для зберігання кількості запитів для кожного користувача
request_counts = {}

# Словник для зберігання часу останнього створення контакту для кожного користувача
last_contact_time = {}

# Словник для зберігання інформації про зареєстрованих користувачів та статусу їх верифікації
users = {}

# Налаштування підключення до Cloudinary
cloudinary.config(
    cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME'),
    api_key=os.getenv('CLOUDINARY_API_KEY'),
    api_secret=os.getenv('CLOUDINARY_API_SECRET')
)

# Додавання CORS middleware
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Функція для надсилання електронної пошти з посиланням для верифікації


def send_verification_email(email, token):
    print(f"Sending verification email to: {email}")
    print(f"Verification token: {token}")
    print("Email sent successfully")

# Функція для перевірки можливості створення контакту користувачем


def can_create_contact(user_id: str, per: timedelta):
    """
    Перевіряє, чи може користувач створити новий контакт.
    """
    # Якщо користувач вже створював контакт раніше
    if user_id in last_contact_time:
        last_contact = last_contact_time[user_id]
        time_elapsed = datetime.now() - last_contact

        # Перевіряємо, чи не минув час, встановлений між створенням контактів
        if time_elapsed < per:
            return False

    # Якщо час відстані від останнього створення контакту більше за період, можна створювати новий контакт
    return True

# Декоратор для обмеження кількості запитів


def rate_limit(limit: int, per: timedelta):
    def decorator(func):
        @wraps(func)
        async def wrapper(request: Request, *args, **kwargs):
            # Отримуємо ідентифікатор користувача
            user_id = request.client.host

            # Якщо користувач вже зробив запити раніше
            if user_id in request_counts:
                last_request_time = request_counts[user_id]
                time_elapsed = datetime.now() - last_request_time

                # Перевіряємо, чи не минув час, встановлений між запитами
                if time_elapsed < per:
                    time_to_wait = per - time_elapsed
                    raise HTTPException(status_code=429, detail=f"Too Many Requests. Try again in {
                                        time_to_wait.total_seconds()} seconds.")

            # Оновлюємо час останнього запиту користувача
            request_counts[user_id] = datetime.now()

            # Викликаємо оригінальну функцію
            return await func(request, *args, **kwargs)

        return wrapper

    return decorator

# Модель для реєстрації нового користувача


class UserRegistration(BaseModel):
    email: str

# Маршрут для реєстрації нового користувача


@app.post("/register/")
async def register_user(user_data: UserRegistration, background_tasks: BackgroundTasks):
    # Перевірка правильності вказаної електронної адреси
    _, email_address = parseaddr(user_data.email)
    if not email_address or '@' not in user_data.email:
        raise HTTPException(status_code=400, detail="Invalid email address")

    # Створення токену для верифікації
    verification_token = str(uuid4())

    # Збереження користувача та статусу верифікації
    users[user_data.email] = {"verified": False,
                              "verification_token": verification_token}

    # Надсилання електронної пошти з посиланням для верифікації
    background_tasks.add_task(send_verification_email,
                              user_data.email, verification_token)

    return {"message": "User registered successfully. Verification email sent."}

# Маршрут для підтвердження електронної пошти


@app.post("/verify/")
async def verify_email(email: str, token: str):
    # Перевірка наявності користувача та правильності токену верифікації
    if email not in users or users[email]["verification_token"] != token:
        raise HTTPException(status_code=400, detail="Invalid email or token")

    # Оновлення статусу верифікації
    users[email]["verified"] = True

    return {"message": "Email verified successfully"}

# Модель для створення контакту


class ContactCreate(BaseModel):
    email: str

# Маршрут для створення контакту


@app.post("/create_contact/")
# Обмеження до 5 запитів на хвилину
@rate_limit(limit=5, per=timedelta(minutes=1))
async def create_contact(request: Request, contact_data: ContactCreate, avatar: UploadFile = File(...)):
    # Отримання даних про користувача
    user_email = contact_data.email

    # Перевірка чи зареєстрований користувач та чи він верифікований
    if user_email not in users or not users[user_email]["verified"]:
        raise HTTPException(
            status_code=401, detail="User not registered or not verified")

    # Перевірка можливості створення контакту
    if not can_create_contact(user_email, timedelta(minutes=1)):
        raise HTTPException(
            status_code=429, detail="Too Many Requests. Try again later.")

    # Збереження часу створення контакту
    last_contact_time[user_email] = datetime.now()

    # Збереження аватара в Cloudinary та отримання посилання на нього
    upload_result = cloudinary.uploader.upload(avatar.file)
    avatar_url = upload_result['secure_url']

    # Повернення відповіді з посиланням на аватар
    return {"message": "Contact created successfully", "avatar_url": avatar_url}
