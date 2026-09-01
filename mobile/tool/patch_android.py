from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]
manifest = root / 'android/app/src/main/AndroidManifest.xml'
text = manifest.read_text(encoding='utf-8')

if 'android.permission.INTERNET' not in text:
    text = text.replace(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android">',
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n    <uses-permission android:name="android.permission.INTERNET" />',
    )

text = re.sub(r'android:label="[^"]*"', 'android:label="WY Wallet"', text, count=1)

intent = '''
            <intent-filter>
                <action android:name="android.intent.action.VIEW" />
                <category android:name="android.intent.category.DEFAULT" />
                <category android:name="android.intent.category.BROWSABLE" />
                <data android:scheme="com.wuyaoenterprise.wywallet" android:host="login-callback" />
            </intent-filter>'''

if 'com.wuyaoenterprise.wywallet' not in text:
    idx = text.find('        </activity>')
    if idx == -1:
        raise RuntimeError('Could not find Android activity in manifest')
    text = text[:idx] + intent + '\n' + text[idx:]

manifest.write_text(text, encoding='utf-8')

for gradle_name in ('build.gradle.kts', 'build.gradle'):
    gradle = root / 'android/app' / gradle_name
    if not gradle.exists():
        continue
    g = gradle.read_text(encoding='utf-8')
    g = g.replace('minSdk = flutter.minSdkVersion', 'minSdk = 24')
    g = re.sub(r'minSdkVersion\s+flutter\.minSdkVersion', 'minSdkVersion 24', g)
    gradle.write_text(g, encoding='utf-8')
