#!/usr/bin/env python3
import subprocess, os, sys

# Build
subprocess.run(["python3", "-c", """
import sys
sys.path.insert(0, '/opt/arm/bin')
from apk_stream_builder import APKStreamBuilder

builder = APKStreamBuilder()
builder.build({
    'app_name': 'ARM Official',
    'package': 'com.arm.official',
    'activity': 'MainActivity',
    'java_code': """
package com.arm.official;
import android.app.*;
import android.os.*;
import android.widget.*;
import android.view.*;
import android.graphics.*;
import android.util.TypedValue;

public class MainActivity extends Activity {
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.parseColor("#000000"));
        root.setGravity(Gravity.CENTER);
        
        TextView tv = new TextView(this);
        tv.setText("ARM OFFICIAL\nBuilt on GitHub\nSigned Officially");
        tv.setTextSize(TypedValue.COMPLEX_UNIT_SP, 24);
        tv.setTextColor(Color.parseColor("#00ff00"));
        tv.setGravity(Gravity.CENTER);
        tv.setLineSpacing(1.6f, 1f);
        
        root.addView(tv);
        setContentView(root);
    }
}
""",
    'output': 'ARM_Official.apk',
    'min_sdk': 21,
    'target_sdk': 33,
})
"""])

print("APK Built!")
