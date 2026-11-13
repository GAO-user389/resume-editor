"""本地直接测 SMTP 连通性 —— 跳过 Render，看 QQ 邮箱配置是否正确。"""
import smtplib, ssl
from email.mime.text import MIMEText
from email.header import Header

# 假设这是你 Render 上配的值
SMTP_HOST = "smtp.qq.com"
SMTP_PORT = 465
EMAIL_USER = "2661047582@qq.com"
EMAIL_PASSWORD = ""  # ⚠️ 你得把授权码贴到这里来测

print("=== QQ 邮箱 SMTP 连通性测试 ===\n")
print(f"SMTP: {SMTP_HOST}:{SMTP_PORT}")
print(f"USER: {EMAIL_USER}")
print(f"PASSWORD: {'已填' if EMAIL_PASSWORD else '⚠️ 空的！请把 16 位授权码填进来'}")

if not EMAIL_PASSWORD:
    print("\n❌ EMAIL_PASSWORD 没配 —— 请把你的 QQ 邮箱 SMTP 授权码填到上面")
    exit(1)

try:
    print("\n1. 连接 smtp.qq.com:465 (SSL)...", flush=True)
    context = ssl.create_default_context()
    server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=15)
    print("   ✅ 连接成功")

    print("2. 登录...", flush=True)
    server.login(EMAIL_USER, EMAIL_PASSWORD)
    print("   ✅ 登录成功")

    print("3. 发测试邮件...", flush=True)
    msg = MIMEText("这是一封 SMTP 连通性测试邮件。如果收到说明配置正确。", "plain", "utf-8")
    msg["Subject"] = Header("简历编辑器 SMTP 测试", "utf-8")
    msg["From"] = f"简历编辑器 <{EMAIL_USER}>"
    msg["To"] = EMAIL_USER  # 发给自己

    server.sendmail(EMAIL_USER, [EMAIL_USER], msg.as_string())
    print("   ✅ 发送成功！检查一下收件箱")

    server.quit()
    print("\n🎉 全部通过！QQ 邮箱 SMTP 配置没问题")
except Exception as e:
    print(f"\n❌ 失败: {type(e).__name__}: {e}")
    print("\n常见原因：")
    print("  - 授权码不对（不是 QQ 登录密码，是邮箱设置里生成的 16 位码）")
    print("  - QQ 邮箱没开 SMTP 服务")
    print("  - Render Free 版出网限制（Render 可能屏蔽了 465 端口）")
