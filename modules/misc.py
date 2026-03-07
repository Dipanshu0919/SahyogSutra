def email_send_message(otp):
    message = f"""
        <div style="font-family: Arial, sans-serif; background-color:#f4f6f8; padding:40px;">
            <div style="max-width:500px; margin:auto; background:white; border-radius:10px; padding:30px; text-align:center; box-shadow:0 4px 10px rgba(0,0,0,0.08);">

                <h2 style="color:#2c3e50; margin-bottom:10px;">
                    Welcome to SahyogSutra 🤝
                </h2>

                <p style="font-size:16px; color:#555;">
                    Thank you for joining our community!
                </p>

                <p style="font-size:16px; color:#555;">
                    Use the OTP below to complete your signup.
                </p>

                <div style="
                    margin:25px 0;
                    padding:15px;
                    font-size:28px;
                    font-weight:bold;
                    letter-spacing:4px;
                    background:#f1f3f5;
                    border-radius:8px;
                    color:#2c3e50;
                ">
                    {otp}
                </div>

                <p style="color:#777; font-size:14px;">
                    This OTP will expire in a few minutes.
                </p>

                <hr style="margin:25px 0; border:none; border-top:1px solid #eee;">

                <p style="font-size:13px; color:#999;">
                    If you didn’t request this email, please ignore it.
                </p>

                <p style="font-size:14px; color:#2c3e50;">
                    <strong>Team SahyogSutra</strong>
                </p>

            </div>
        </div>
        """

    return message
