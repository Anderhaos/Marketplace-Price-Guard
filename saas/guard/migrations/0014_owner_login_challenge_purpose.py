from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("guard", "0013_promotion_snapshot")]
    operations = [migrations.AlterField(model_name="emailpinchallenge", name="purpose", field=models.CharField(max_length=16, choices=[("register", "Регистрация"), ("recover", "Восстановление PIN"), ("owner", "Подтверждение входа владельца")]))]
