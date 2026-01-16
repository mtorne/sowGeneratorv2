from typing import Dict, List

class ResponseFormatter:
    """Utility class for formatting API responses"""
    
    @staticmethod
    def format_html_response(
        customer: str,
        application: str,
        scope: str,
        final_text: str,
        placeholders: List[str],
        has_diagram: bool
    ) -> str:
        """Format content as HTML response"""
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Generated Document - {customer} - {application}</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body {{ 
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
                    max-width: 1000px; 
                    margin: 0 auto; 
                    padding: 20px; 
                    line-height: 1.6;
                    background-color: #f8f9fa;
                }}
                .header {{ 
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    padding: 25px; 
                    border-radius: 10px; 
                    margin-bottom: 25px;
                    box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                }}
                .header h1 {{
                    margin: 0 0 15px 0;
                    font-size: 2em;
                }}
                .header p {{
                    margin: 5px 0;
                    opacity: 0.9;
                }}
                .content {{ 
                    white-space: pre-wrap; 
                    background-color: white; 
                    padding: 25px; 
                    border-radius: 10px;
                    border-left: 5px solid #667eea;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                    font-size: 1.1em;
                }}
                .metadata {{ 
                    background-color: white; 
                    padding: 20px; 
                    border-radius: 10px; 
                    margin-bottom: 25px;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                }}
                .metadata h3 {{
                    color: #333;
                    margin-top: 0;
                    border-bottom: 2px solid #667eea;
                    padding-bottom: 10px;
                }}
                .badge {{
                    background-color: #e3f2fd;
                    color: #1976d2;
                    padding: 4px 8px;
                    border-radius: 4px;
                    font-size: 0.9em;
                    margin-right: 5px;
                }}
                .success {{ background-color: #e8f5e8; color: #2e7d32; }}
                .info {{ background-color: #fff3e0; color: #f57c00; }}
            </style>
        </head>
        <body>
            <div class="header">
                <h1>📋 Generated Document</h1>
                <p><strong>Customer:</strong> {customer}</p>
                <p><strong>Application:</strong> {application}</p>
                <p><strong>Scope:</strong> {scope}</p>
            </div>
            
            <div class="metadata">
                <h3>🔍 Processing Information</h3>
                <p><strong>Placeholders Processed:</strong> 
                    {' '.join(f'<span class="badge">{p}</span>' for p in placeholders) if placeholders else '<span class="badge info">None found</span>'}
                </p>
                <p><strong>Diagram Analysis:</strong> 
                    <span class="badge {'success' if has_diagram else 'info'}">
                        {'✅ Completed' if has_diagram else '❌ No diagram provided'}
                    </span>
                </p>
            </div>
            
            <div class="content">
                <h3>📄 Generated Content</h3>
                {final_text.replace(chr(10), '<br>')}
            </div>
        </body>
        </html>
        """
    
    @staticmethod
    def format_error_html(error_message: str) -> str:
        """Format error as HTML response"""
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Error - Document Generation</title>
            <meta charset="utf-8">
            <style>
                body {{ 
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
                    max-width: 800px; 
                    margin: 50px auto; 
                    padding: 20px; 
                }}
                .error {{ 
                    background: linear-gradient(135deg, #ff6b6b, #ee5a52);
                    color: white;
                    padding: 25px; 
                    border-radius: 10px;
                    box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                }}
                .error h2 {{
                    margin: 0 0 15px 0;
                }}
            </style>
        </head>
        <body>
            <div class="error">
                <h2>❌ Error Processing Request</h2>
                <p><strong>Details:</strong> {error_message}</p>
                <p>Please check your input files and try again.</p>
            </div>
        </body>
        </html>
        """

