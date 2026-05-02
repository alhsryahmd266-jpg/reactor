import 'package:flutter/material.dart';
import 'package:webview_flutter/webview_flutter.dart';
void main(){WidgetsFlutterBinding.ensureInitialized();runApp(const AuraApp());}
class AuraApp extends StatelessWidget{const AuraApp({super.key});
@override Widget build(BuildContext c)=>MaterialApp(debugShowCheckedModeBanner:false,home:const AuraView());}
class AuraView extends StatefulWidget{const AuraView({super.key});
@override State<AuraView> createState()=>_S();}
class _S extends State<AuraView>{
late final WebViewController _c;
final String _h='<!DOCTYPE html>\n<html lang="ar" dir="rtl">\n<head>\n    <meta charset="UTF-8">\n    <meta name="viewport" content="width=device-width, initial-scale=1.0">\n    <title>جمعه ويه apk</title>\n    <link rel="icon" href="favicon.ico" type="image/x-icon"> <!-- FIXED: Added favicon link. Ensure \'favicon.ico\' exists in the root directory. -->\n    <link href="https://fonts.googleapis.com/css2?family=Cairo:wght@300;400;700&display=swap" rel="stylesheet">\n    <!-- Font Awesome for icons -->\n    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/css/all.min.css">\n    <style>\n        /* CSS Variables for theming */\n        :root {\n            --primary-color: #008; /* Dark Blue */\n            --accent-color: #00bfff; /* Electric Blue for highlights */\n            --text-color: #e0e0e0; /* Light grey for';
@override void initState(){super.initState();_c=WebViewController()..setJavaScriptMode(JavaScriptMode.unrestricted)..loadHtmlString(_h);}
@override Widget build(BuildContext c)=>Scaffold(body:SafeArea(child:WebViewWidget(controller:_c)));}