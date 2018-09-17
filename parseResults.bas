Attribute VB_Name = "parseResults"
Public Sub Clear_results()
    For i = 1 To 6
        Dim xlrow As Integer
        xlrow = 2
        
        'Copy values
        Do Until IsEmpty(ActiveWorkbook.Sheets(i).Cells(xlrow, 10).Value)
            ActiveWorkbook.Sheets(i).Cells(xlrow, 7).Value = ""
            
            xlrow = xlrow + 1
        Loop
    Next
End Sub

Public Sub Parse_Results()
    For i = 0 To 5
        Dim source_file As String
        If i = 0 Then
            source_file = "results_phantom.csv"
        Else
            source_file = "results_case" & CStr(i) & ".csv"
        End If
        
        parse_file ActiveWorkbook.Path & "\results\" & source_file, ActiveWorkbook.Sheets(i + 1)
    Next
End Sub

Sub parse_file(ByVal fname As String, ByVal targetSheet As Worksheet)
    Dim source_book As Workbook
    Set source_book = Workbooks.Open(fname)
    Dim source_sheet As Worksheet
    Set source_sheet = source_book.Sheets(1)
    
    On Error GoTo cleanup
    
    Dim clmn As Integer
    clmn = 2
    
    Dim Tag_column As Integer
    
    Do Until IsEmpty(source_sheet.Cells(1, clmn).Value)
        If source_sheet.Cells(1, clmn).Value = "tag" Then
            Tag_column = clmn
        End If
        
        clmn = clmn + 1
    Loop
    
    clmn = clmn - 1 'last column is value column
    
    
    Dim xlrow As Integer
    xlrow = 2
    
    'Copy values
    Do Until IsEmpty(targetSheet.Cells(xlrow, 10).Value)
        If targetSheet.Cells(xlrow, 10).Value <> source_sheet.Cells(xlrow, Tag_column).Value Then
            MsgBox "Tag mismatch! " & targetSheet.Cells(xlrow, 10).Value & " != " & source_sheet.Cells(xlrow, Tag_column).Value
        End If
        
        targetSheet.Cells(xlrow, 7).Value = source_sheet.Cells(xlrow, clmn).Value
        
        xlrow = xlrow + 1
    Loop
cleanup:
    source_book.Close
End Sub
