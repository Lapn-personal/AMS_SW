import time
import board
import busio
import adafruit_mpu6050

# Инициализация I2C шины и датчика
# Явно указываем пины, как мы делали для датчика освещенности
i2c = busio.I2C(board.SCL, board.SDA)
mpu = adafruit_mpu6050.MPU6050(i2c)

print("Мониторинг MPU6050. Нажмите Ctrl+C для выхода.\n")

try:
    while True:
        # Получаем данные с акселерометра (в м/с^2)
        accel_x, accel_y, accel_z = mpu.acceleration
        # Получаем данные с гироскопа (в градусах/с)
        gyro_x, gyro_y, gyro_z = mpu.gyro
        # Получаем температуру (в градусах Цельсия)
        temperature = mpu.temperature

        # Выводим данные в красивом формате
        print(f"Accel: X={accel_x:.2f}, Y={accel_y:.2f}, Z={accel_z:.2f} m/s^2")
        print(f"Gyro:  X={gyro_x:.2f}, Y={gyro_y:.2f}, Z={gyro_z:.2f} deg/s")
        print(f"Temp:  {temperature:.2f} C")
        print("-" * 40)

        time.sleep(0.5)  # Задержка в полсекунды
except KeyboardInterrupt:
    print("\nПрограмма завершена.")
